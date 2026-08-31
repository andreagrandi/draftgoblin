"""Acquire normalized card metadata for profile generation.
Keep cache, provenance, and failure outcomes deterministic and path-free.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from io import BytesIO
from typing import Any, Protocol

from draftomen.carddb import (
    HTTP_TIMEOUT_SECONDS,
    CardDatabase,
    CardDatabaseError,
    download_scryfall_card_database,
)
from draftomen.profile_input_cache import (
    ProfileInputCache,
    ProfileInputCacheError,
    ProfileInputCacheOutcome,
    ProfileInputCacheResult,
    ProfileInputSource,
)
from draftomen.refresh_plan import PlannedEnvironment


PROFILE_INPUT_ACQUISITION_SCHEMA_VERSION = 1
CARD_METADATA_ADAPTER_VERSION = 1
CARD_METADATA_SOURCE_NAME = "card-metadata"
Clock = Callable[[], datetime]


class ProfileInputAcquisitionError(ValueError):
    """Report invalid acquisition arguments or normalized source data.
    Operational source failures are returned as bounded outcomes instead.
    """


class ProfileInputAcquisitionOutcome(str, Enum):
    """Describe how a usable or unavailable profile input was resolved.
    Values distinguish acquisition from cache and failure behavior.
    """

    ACQUIRED = "acquired"
    CACHED = "cached"
    OFFLINE_REUSED = "offline-reused"
    STALE = "stale"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


class CardDatabaseFetcher(Protocol):
    """Fetch a card database for a normalized set identity.
    Implementations accept named parameters so tests remain explicit.
    """

    def __call__(self, *, set_code: str, timeout_seconds: int) -> CardDatabase:
        ...


def _fetch_default_card_database(*, set_code: str, timeout_seconds: int) -> CardDatabase:
    del set_code
    return download_scryfall_card_database(timeout_seconds=timeout_seconds)


@dataclass(frozen=True, slots=True)
class ProfileInputSourceReport:
    """Record one source decision using portable provenance only.
    Paths, card rows, credentials, and exception text are never retained.
    """

    source: ProfileInputSource
    outcome: ProfileInputAcquisitionOutcome
    cache_lookup_outcome: ProfileInputCacheOutcome | None
    cache_store_outcome: ProfileInputCacheOutcome | None = None
    source_version: str | None = None
    acquired_at: datetime | None = None
    sha256: str | None = None
    content_bytes: int | None = None
    card_count: int = 0
    diagnostics: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """Return deterministic provenance and sample availability.
        Optional cache-record fields appear only for verified inputs.
        """

        value: dict[str, Any] = {
            "cache_lookup_outcome": (
                None if self.cache_lookup_outcome is None else self.cache_lookup_outcome.value
            ),
            "cache_store_outcome": (
                None if self.cache_store_outcome is None else self.cache_store_outcome.value
            ),
            "diagnostics": list(self.diagnostics),
            "outcome": self.outcome.value,
            "sample_availability": {"card_count": self.card_count},
            "source": self.source.to_json(),
        }
        if self.source_version is not None:
            value["source_version"] = self.source_version
        if self.acquired_at is not None:
            value["acquired_at"] = self.acquired_at.astimezone(UTC).isoformat()
        if self.sha256 is not None:
            value["sha256"] = self.sha256
        if self.content_bytes is not None:
            value["content_bytes"] = self.content_bytes
        return value


@dataclass(frozen=True, slots=True)
class ProfileBuildBundle:
    """Carry normalized inputs required by the existing profile generator.
    Raw card metadata stays in memory and out of acquisition reports.
    """

    environment: PlannedEnvironment
    card_database: CardDatabase = field(repr=False)
    card_metadata: ProfileInputSourceReport

    def __post_init__(self) -> None:
        if self.card_metadata.source.set_code != self.environment.set_code:
            raise ProfileInputAcquisitionError("Card metadata source does not match the bundle.")
        _validate_card_database(
            database=self.card_database,
            environment=self.environment,
            acquired_at=self.card_metadata.acquired_at,
        )

    def generator_inputs(self) -> dict[str, object]:
        """Return the values accepted by the existing profile generator.
        Stage, timestamp, and publication choices remain caller inputs.
        """

        return {
            "card_database": self.card_database,
            "event_format": self.environment.event_format,
            "set_code": self.environment.set_code,
        }


@dataclass(frozen=True, slots=True)
class ProfileInputAcquisitionResult:
    """Return a usable bundle or a bounded required-source failure.
    Serialized reports expose source facts without the bundle contents.
    """

    environment: PlannedEnvironment
    source: ProfileInputSourceReport
    bundle: ProfileBuildBundle | None = field(default=None, repr=False)
    skip_reasons: tuple[str, ...] = ()
    schema_version: int = PROFILE_INPUT_ACQUISITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROFILE_INPUT_ACQUISITION_SCHEMA_VERSION:
            raise ProfileInputAcquisitionError("Unsupported profile-input acquisition schema.")
        object.__setattr__(self, "skip_reasons", tuple(sorted(set(self.skip_reasons))))

    @property
    def succeeded(self) -> bool:
        """Return whether required metadata produced a build bundle.
        Stale and offline-reused verified inputs remain successful.
        """

        return self.bundle is not None

    def to_json(self) -> dict[str, Any]:
        """Return a deterministic path-free acquisition report.
        The bundle is represented only by its availability flag.
        """

        return {
            "bundle_available": self.succeeded,
            "environment": self.environment.to_json(),
            "schema_version": self.schema_version,
            "skip_reasons": list(self.skip_reasons),
            "sources": [self.source.to_json()],
        }

    def to_bytes(self) -> bytes:
        """Serialize the acquisition report as canonical JSON.
        Output has sorted keys, compact separators, and one final newline.
        """

        return (
            json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _CardMetadataSnapshot:
    database: CardDatabase
    payload: bytes
    source_version: str
    acquired_at: datetime
    sha256: str


@dataclass(frozen=True, slots=True)
class CardMetadataAdapter:
    """Fetch and normalize set-scoped card metadata into cacheable bytes.
    The default uses Scryfall bulk data without its separate runtime cache.
    """

    fetch_database: CardDatabaseFetcher = field(default=_fetch_default_card_database)
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS
    source_name: str = CARD_METADATA_SOURCE_NAME

    def __post_init__(self) -> None:
        if not callable(self.fetch_database):
            raise ProfileInputAcquisitionError("fetch_database must be callable.")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds <= 0
        ):
            raise ProfileInputAcquisitionError("timeout_seconds must be positive.")
        normalized = ProfileInputSource(name=self.source_name, set_code="TST")
        object.__setattr__(self, "source_name", normalized.name)

    def source_for(self, *, environment: PlannedEnvironment) -> ProfileInputSource:
        """Return the format-independent cache identity for one set.
        Card metadata can be shared across every event format for that set.
        """

        return ProfileInputSource(name=self.source_name, set_code=environment.set_code)

    def acquire(
        self,
        *,
        environment: PlannedEnvironment,
        acquired_at: datetime,
    ) -> _CardMetadataSnapshot:
        """Fetch, select, and serialize the requested set metadata.
        Empty or invalid data fails before the cache is mutated.
        """

        timestamp = _timestamp(acquired_at)
        database = self.fetch_database(
            set_code=environment.set_code,
            timeout_seconds=self.timeout_seconds,
        )
        normalized = _normalize_card_database(
            database=database,
            environment=environment,
            acquired_at=timestamp,
        )
        payload = _card_database_bytes(normalized)
        sha256 = hashlib.sha256(payload).hexdigest()
        version_time = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        return _CardMetadataSnapshot(
            database=normalized,
            payload=payload,
            source_version=f"v{CARD_METADATA_ADAPTER_VERSION}-{version_time}-{sha256}",
            acquired_at=timestamp,
            sha256=sha256,
        )


def acquire_card_metadata_bundle(
    *,
    environment: PlannedEnvironment,
    cache: ProfileInputCache,
    adapter: CardMetadataAdapter | None = None,
    offline: bool = False,
    clock: Clock | None = None,
) -> ProfileInputAcquisitionResult:
    """Resolve required card metadata through the bounded input cache.
    Online refresh preserves a verified stale fallback and reports failure.
    """

    if not isinstance(environment, PlannedEnvironment):
        raise ProfileInputAcquisitionError("environment must be a PlannedEnvironment.")
    if not isinstance(cache, ProfileInputCache):
        raise ProfileInputAcquisitionError("cache must be a ProfileInputCache.")
    selected_adapter = adapter or CardMetadataAdapter()
    source = selected_adapter.source_for(environment=environment)

    try:
        lookup = cache.lookup(source=source, offline=offline)
    except ProfileInputCacheError:
        return _failure(
            environment=environment,
            report=ProfileInputSourceReport(
                source=source,
                outcome=ProfileInputAcquisitionOutcome.UNAVAILABLE,
                cache_lookup_outcome=None,
                diagnostics=("cache-lookup-failed",),
            ),
            reason="card-metadata-cache-unavailable",
        )

    cached, content_diagnostics = _load_cached_database(
        result=lookup,
        environment=environment,
    )
    cache_is_corrupt = lookup.record is not None and cached is None
    if cached is not None and lookup.outcome in {
        ProfileInputCacheOutcome.FRESH,
        ProfileInputCacheOutcome.OFFLINE_REUSED,
    }:
        outcome = (
            ProfileInputAcquisitionOutcome.CACHED
            if lookup.outcome is ProfileInputCacheOutcome.FRESH
            else ProfileInputAcquisitionOutcome.OFFLINE_REUSED
        )
        return _success(
            environment=environment,
            database=cached,
            report=_report_from_cache(
                result=lookup,
                outcome=outcome,
                database=cached,
                diagnostics=content_diagnostics,
            ),
        )

    if offline:
        outcome = (
            ProfileInputAcquisitionOutcome.CORRUPT
            if cache_is_corrupt or lookup.outcome is ProfileInputCacheOutcome.CORRUPT
            else ProfileInputAcquisitionOutcome.MISSING
        )
        reason = (
            "card-metadata-cache-corrupt"
            if outcome is ProfileInputAcquisitionOutcome.CORRUPT
            else "card-metadata-cache-missing"
        )
        return _failure(
            environment=environment,
            report=_report_from_cache(
                result=lookup,
                outcome=outcome,
                diagnostics=content_diagnostics,
            ),
            reason=reason,
        )

    acquired_at = _now(clock)
    try:
        snapshot = selected_adapter.acquire(
            environment=environment,
            acquired_at=acquired_at,
        )
    except Exception:
        return _refresh_failure(
            environment=environment,
            lookup=lookup,
            cached=cached,
            cache_is_corrupt=cache_is_corrupt,
            diagnostics=(*content_diagnostics, "card-metadata-acquisition-failed"),
            stale_reason="card-metadata-refresh-failed",
            unavailable_reason="card-metadata-unavailable",
        )

    try:
        stored = cache.store(
            source=source,
            source_version=snapshot.source_version,
            input_stream=BytesIO(snapshot.payload),
            expected_sha256=snapshot.sha256,
            acquired_at=snapshot.acquired_at,
        )
    except ProfileInputCacheError:
        return _refresh_failure(
            environment=environment,
            lookup=lookup,
            cached=cached,
            cache_is_corrupt=cache_is_corrupt,
            diagnostics=(*content_diagnostics, "card-metadata-cache-store-failed"),
            stale_reason="card-metadata-cache-store-failed",
            unavailable_reason="card-metadata-cache-store-failed",
        )

    return _success(
        environment=environment,
        database=snapshot.database,
        report=_report_from_cache(
            result=stored,
            outcome=ProfileInputAcquisitionOutcome.ACQUIRED,
            database=snapshot.database,
            cache_lookup_outcome=lookup.outcome,
            cache_store_outcome=stored.outcome,
            diagnostics=content_diagnostics,
        ),
    )


def _normalize_card_database(
    *,
    database: CardDatabase,
    environment: PlannedEnvironment,
    acquired_at: datetime,
) -> CardDatabase:
    if not isinstance(database, CardDatabase):
        raise ProfileInputAcquisitionError("Card metadata adapter returned an invalid database.")
    requested_set = environment.set_code.casefold()
    selected = CardDatabase(
        cards={
            grp_id: card
            for grp_id, card in database.cards.items()
            if card.set_code is not None and card.set_code.casefold() == requested_set
        },
        generated_at=acquired_at,
    )
    normalized = CardDatabase.from_json(data=selected.to_json())
    _validate_card_database(
        database=normalized,
        environment=environment,
        acquired_at=acquired_at,
    )
    return normalized


def _validate_card_database(
    *,
    database: CardDatabase,
    environment: PlannedEnvironment,
    acquired_at: datetime | None,
) -> None:
    if not database.cards:
        raise ProfileInputAcquisitionError("Card metadata did not contain the requested set.")
    requested_set = environment.set_code.casefold()
    if any(
        card.set_code is None or card.set_code.casefold() != requested_set
        for card in database.cards.values()
    ):
        raise ProfileInputAcquisitionError("Card metadata contains an unexpected set.")
    if acquired_at is None or database.generated_at is None:
        raise ProfileInputAcquisitionError("Card metadata acquisition timestamp is missing.")
    if _timestamp(database.generated_at) != _timestamp(acquired_at):
        raise ProfileInputAcquisitionError("Card metadata timestamp does not match its record.")


def _card_database_bytes(database: CardDatabase) -> bytes:
    return (
        json.dumps(database.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _load_cached_database(
    *,
    result: ProfileInputCacheResult,
    environment: PlannedEnvironment,
) -> tuple[CardDatabase | None, tuple[str, ...]]:
    if result.record is None or result.content_path is None:
        return None, result.diagnostics
    try:
        payload = result.content_path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, Mapping):
            raise ProfileInputAcquisitionError("Card metadata cache is not an object.")
        database = CardDatabase.from_json(data=value)
        if payload != _card_database_bytes(database):
            raise ProfileInputAcquisitionError("Card metadata cache is not canonical.")
        _validate_card_database(
            database=database,
            environment=environment,
            acquired_at=result.record.acquired_at,
        )
    except (
        CardDatabaseError,
        OSError,
        ProfileInputAcquisitionError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None, (*result.diagnostics, "card-metadata-content-invalid")
    return database, result.diagnostics


def _refresh_failure(
    *,
    environment: PlannedEnvironment,
    lookup: ProfileInputCacheResult,
    cached: CardDatabase | None,
    cache_is_corrupt: bool,
    diagnostics: tuple[str, ...],
    stale_reason: str,
    unavailable_reason: str,
) -> ProfileInputAcquisitionResult:
    if cached is not None and lookup.outcome is ProfileInputCacheOutcome.STALE:
        return _success(
            environment=environment,
            database=cached,
            report=_report_from_cache(
                result=lookup,
                outcome=ProfileInputAcquisitionOutcome.STALE,
                database=cached,
                diagnostics=diagnostics,
            ),
            reason=stale_reason,
        )
    corrupt = cache_is_corrupt or lookup.outcome is ProfileInputCacheOutcome.CORRUPT
    return _failure(
        environment=environment,
        report=_report_from_cache(
            result=lookup,
            outcome=(
                ProfileInputAcquisitionOutcome.CORRUPT
                if corrupt
                else ProfileInputAcquisitionOutcome.UNAVAILABLE
            ),
            diagnostics=diagnostics,
        ),
        reason=("card-metadata-cache-corrupt" if corrupt else unavailable_reason),
    )


def _report_from_cache(
    *,
    result: ProfileInputCacheResult,
    outcome: ProfileInputAcquisitionOutcome,
    database: CardDatabase | None = None,
    cache_lookup_outcome: ProfileInputCacheOutcome | None = None,
    cache_store_outcome: ProfileInputCacheOutcome | None = None,
    diagnostics: tuple[str, ...] = (),
) -> ProfileInputSourceReport:
    record = result.record
    return ProfileInputSourceReport(
        source=result.source,
        outcome=outcome,
        cache_lookup_outcome=(result.outcome if cache_lookup_outcome is None else cache_lookup_outcome),
        cache_store_outcome=cache_store_outcome,
        source_version=None if record is None else record.source_version,
        acquired_at=None if record is None else record.acquired_at,
        sha256=None if record is None else record.sha256,
        content_bytes=None if record is None else record.content_bytes,
        card_count=0 if database is None else len(database),
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def _success(
    *,
    environment: PlannedEnvironment,
    database: CardDatabase,
    report: ProfileInputSourceReport,
    reason: str | None = None,
) -> ProfileInputAcquisitionResult:
    return ProfileInputAcquisitionResult(
        environment=environment,
        source=report,
        bundle=ProfileBuildBundle(
            environment=environment,
            card_database=database,
            card_metadata=report,
        ),
        skip_reasons=() if reason is None else (reason,),
    )


def _failure(
    *,
    environment: PlannedEnvironment,
    report: ProfileInputSourceReport,
    reason: str,
) -> ProfileInputAcquisitionResult:
    return ProfileInputAcquisitionResult(
        environment=environment,
        source=report,
        skip_reasons=(reason,),
    )


def _timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProfileInputAcquisitionError("Acquisition timestamps must be timezone-aware.")
    return value.astimezone(UTC)


def _now(clock: Clock | None) -> datetime:
    if clock is not None and not callable(clock):
        raise ProfileInputAcquisitionError("clock must be callable.")
    return _timestamp(datetime.now(tz=UTC) if clock is None else clock())


__all__ = [
    "CARD_METADATA_ADAPTER_VERSION",
    "CARD_METADATA_SOURCE_NAME",
    "PROFILE_INPUT_ACQUISITION_SCHEMA_VERSION",
    "CardDatabaseFetcher",
    "CardMetadataAdapter",
    "ProfileBuildBundle",
    "ProfileInputAcquisitionError",
    "ProfileInputAcquisitionOutcome",
    "ProfileInputAcquisitionResult",
    "ProfileInputSourceReport",
    "acquire_card_metadata_bundle",
]
