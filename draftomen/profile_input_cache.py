"""Bounded, content-addressed cache for immutable profile inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, TypeAlias


PROFILE_INPUT_CACHE_SCHEMA_VERSION = 1
PathInput: TypeAlias = str | os.PathLike[str]
Clock: TypeAlias = Callable[[], datetime]
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK_SIZE = 1024 * 1024


class ProfileInputCacheError(RuntimeError):
    """Raised when profile-input cache data or a cache mutation is invalid."""


class ProfileInputCacheCapacityError(ProfileInputCacheError):
    """Raised when a mutation cannot satisfy the configured cache bounds."""


class ProfileInputCacheConflictError(ProfileInputCacheError):
    """Raised when a source/version is already pinned to different bytes."""


class ProfileInputCacheOutcome(str, Enum):
    """Outcome of a verified profile-input cache lookup."""

    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    CORRUPT = "corrupt"
    OFFLINE_REUSED = "offline-reused"




def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileInputCacheError(f"{field_name} must be a non-empty string.")
    return value.strip()



def _safe_component(value: Any, field_name: str, *, casefold: bool = True) -> str:
    normalized = _string(value, field_name)
    if casefold:
        normalized = normalized.casefold()
    if normalized in {".", ".."} or any(
        character in normalized for character in ("/", "\\", "\x00", ":", "<", ">", '"', "|", "?", "*")
    ) or any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in normalized
    ):
        raise ProfileInputCacheError(f"{field_name} must be a safe portable value.")
    return normalized



def _optional_component(value: Any, field_name: str, *, casefold: bool = True) -> str | None:
    if value is None:
        return None
    return _safe_component(value, field_name, casefold=casefold)



def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProfileInputCacheError(f"{field_name} must be a positive integer.")
    return value



def _nonnegative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProfileInputCacheError(f"{field_name} must be a non-negative integer.")
    return value



def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProfileInputCacheError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)



def _timestamp_from_json(value: Any, field_name: str) -> datetime:
    text = _string(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ProfileInputCacheError(f"{field_name} must be an ISO-8601 timestamp.") from error
    return _timestamp(parsed, field_name)



def _hash(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value.casefold()) is None:
        raise ProfileInputCacheError(f"{field_name} must be a SHA-256 digest.")
    return value.casefold()



def _required(value: Mapping[str, Any], key: str) -> Any:
    if key not in value:
        raise ProfileInputCacheError(f"Missing required profile-input cache field {key!r}.")
    return value[key]



def _keys(value: Mapping[str, Any], expected: set[str], field_name: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        names = ", ".join(sorted(repr(item) for item in unknown))
        raise ProfileInputCacheError(f"{field_name} contains unsupported fields: {names}.")
    if missing:
        names = ", ".join(sorted(repr(item) for item in missing))
        raise ProfileInputCacheError(f"{field_name} is missing fields: {names}.")



def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return (serialized + "\n").encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise ProfileInputCacheError("Could not canonicalize profile-input cache data.") from error



def _normalize_version(value: Any) -> str:
    return _safe_component(value, "source_version", casefold=False)


@dataclass(frozen=True, slots=True)
class ProfileInputSource:
    """Portable logical identity for one profile-input source."""

    name: str
    set_code: str | None = None
    event_format: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _safe_component(self.name, "source name"))
        set_code = _optional_component(self.set_code, "source set_code", casefold=False)
        object.__setattr__(self, "set_code", set_code.upper() if set_code is not None else None)
        object.__setattr__(self, "event_format", _optional_component(self.event_format, "source event_format"))

    def to_json(self) -> dict[str, str | None]:
        """Return the complete canonical source identity."""

        return {"event_format": self.event_format, "name": self.name, "set_code": self.set_code}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ProfileInputSource:
        """Parse one source identity with no extension fields."""

        if not isinstance(value, Mapping):
            raise ProfileInputCacheError("Profile-input cache source must be an object.")
        _keys(value, {"event_format", "name", "set_code"}, "profile-input cache source")
        return cls(
            name=_required(value, "name"),
            set_code=_required(value, "set_code"),
            event_format=_required(value, "event_format"),
        )


@dataclass(frozen=True, slots=True)
class ProfileInputCachePolicy:
    """Explicit limits controlling freshness and cache retention."""

    freshness_ttl: timedelta
    max_entry_bytes: int
    max_total_bytes: int
    max_records: int
    max_versions_per_source: int

    def __post_init__(self) -> None:
        if not isinstance(self.freshness_ttl, timedelta) or self.freshness_ttl <= timedelta(0):
            raise ProfileInputCacheError("freshness_ttl must be positive.")
        object.__setattr__(self, "max_entry_bytes", _positive_integer(self.max_entry_bytes, "max_entry_bytes"))
        object.__setattr__(self, "max_total_bytes", _positive_integer(self.max_total_bytes, "max_total_bytes"))
        object.__setattr__(self, "max_records", _positive_integer(self.max_records, "max_records"))
        object.__setattr__(
            self,
            "max_versions_per_source",
            _positive_integer(self.max_versions_per_source, "max_versions_per_source"),
        )
        if self.max_entry_bytes > self.max_total_bytes:
            raise ProfileInputCacheError("max_entry_bytes cannot exceed max_total_bytes.")


@dataclass(frozen=True, slots=True)
class ProfileInputCacheRecord:
    """Canonical metadata pinning one immutable profile-input object."""

    schema_version: int
    source: ProfileInputSource
    source_version: str
    acquired_at: datetime
    sha256: str
    content_bytes: int

    def __post_init__(self) -> None:
        schema_version = _positive_integer(self.schema_version, "schema_version")
        if schema_version != PROFILE_INPUT_CACHE_SCHEMA_VERSION:
            raise ProfileInputCacheError(
                f"Unsupported profile-input cache schema {schema_version}; "
                f"expected {PROFILE_INPUT_CACHE_SCHEMA_VERSION}."
            )
        if not isinstance(self.source, ProfileInputSource):
            raise ProfileInputCacheError("source must be a ProfileInputSource.")
        object.__setattr__(self, "source_version", _normalize_version(self.source_version))
        object.__setattr__(self, "acquired_at", _timestamp(self.acquired_at, "acquired_at"))
        object.__setattr__(self, "sha256", _hash(self.sha256, "sha256"))
        object.__setattr__(self, "content_bytes", _nonnegative_integer(self.content_bytes, "content_bytes"))
        object.__setattr__(self, "schema_version", schema_version)

    def to_json(self) -> dict[str, Any]:
        return {
            "acquired_at": self.acquired_at.isoformat(),
            "content_bytes": self.content_bytes,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "source": self.source.to_json(),
            "source_version": self.source_version,
        }

    def to_bytes(self) -> bytes:
        """Return strict canonical sidecar bytes."""

        return _canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ProfileInputCacheRecord:
        """Parse one strict profile-input cache sidecar record."""

        if not isinstance(value, Mapping):
            raise ProfileInputCacheError("Profile-input cache record must be an object.")
        _keys(
            value,
            {"acquired_at", "content_bytes", "schema_version", "sha256", "source", "source_version"},
            "profile-input cache record",
        )
        schema_version = _required(value, "schema_version")
        if schema_version != PROFILE_INPUT_CACHE_SCHEMA_VERSION:
            raise ProfileInputCacheError(
                f"Unsupported profile-input cache schema {schema_version}; "
                f"expected {PROFILE_INPUT_CACHE_SCHEMA_VERSION}."
            )
        return cls(
            schema_version=schema_version,
            source=ProfileInputSource.from_json(_required(value, "source")),
            source_version=_required(value, "source_version"),
            acquired_at=_timestamp_from_json(_required(value, "acquired_at"), "acquired_at"),
            sha256=_required(value, "sha256"),
            content_bytes=_required(value, "content_bytes"),
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> ProfileInputCacheRecord:
        """Parse and reject any non-canonical sidecar bytes."""

        if not isinstance(payload, bytes):
            raise ProfileInputCacheError("Profile-input cache record bytes must be bytes.")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise ProfileInputCacheError("Could not parse profile-input cache record.") from error
        record = cls.from_json(value)
        if payload != record.to_bytes():
            raise ProfileInputCacheError("Profile-input cache record is not canonical.")
        return record


@dataclass(frozen=True, slots=True)
class ProfileInputCacheResult:
    """Privacy-safe result for one profile-input cache lookup."""

    source: ProfileInputSource
    requested_version: str | None
    outcome: ProfileInputCacheOutcome | str
    record: ProfileInputCacheRecord | None = None
    content_path: Path | None = field(default=None, repr=False, compare=False)
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source, ProfileInputSource):
            raise ProfileInputCacheError("source must be a ProfileInputSource.")
        if self.requested_version is not None:
            object.__setattr__(self, "requested_version", _normalize_version(self.requested_version))
        try:
            outcome = self.outcome if isinstance(self.outcome, ProfileInputCacheOutcome) else ProfileInputCacheOutcome(self.outcome)
        except (TypeError, ValueError) as error:
            raise ProfileInputCacheError("Unsupported profile-input cache outcome.") from error
        object.__setattr__(self, "outcome", outcome)
        if self.record is not None and not isinstance(self.record, ProfileInputCacheRecord):
            raise ProfileInputCacheError("record must be a ProfileInputCacheRecord or None.")
        if self.content_path is not None and not isinstance(self.content_path, Path):
            object.__setattr__(self, "content_path", Path(self.content_path))
        object.__setattr__(self, "diagnostics", tuple(dict.fromkeys(str(item) for item in self.diagnostics)))

    @property
    def status(self) -> str:
        """Return the compact outcome value."""

        return self.outcome.value


    def to_json(self) -> dict[str, Any]:
        """Return diagnostics and metadata without paths or input content."""

        value: dict[str, Any] = {
            "cache_outcome": self.outcome.value,
            "diagnostics": list(self.diagnostics),
            "source": self.source.to_json(),
        }
        if self.requested_version is not None:
            value["requested_version"] = self.requested_version
        if self.record is not None:
            value.update(
                {
                    "acquired_at": self.record.acquired_at.isoformat(),
                    "content_bytes": self.record.content_bytes,
                    "sha256": self.record.sha256,
                    "source_version": self.record.source_version,
                }
            )
        return value


@dataclass(frozen=True, slots=True)
class ProfileInputCachePruneResult:
    """Path-free counts returned by pruning and invalidation."""

    deleted_records: int = 0
    deleted_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "deleted_records", _nonnegative_integer(self.deleted_records, "deleted_records"))
        object.__setattr__(self, "deleted_bytes", _nonnegative_integer(self.deleted_bytes, "deleted_bytes"))

    @property
    def records(self) -> int:
        """Return the number of deleted sidecar records."""

        return self.deleted_records

    @property
    def bytes(self) -> int:
        """Return the number of deleted content bytes."""

        return self.deleted_bytes

    @property
    def removed_records(self) -> int:
        """Return the number of deleted sidecar records."""

        return self.deleted_records

    @property
    def removed_bytes(self) -> int:
        """Return the number of deleted content bytes."""

        return self.deleted_bytes

    def to_json(self) -> dict[str, int]:
        """Return path-free maintenance counts."""

        return {"deleted_bytes": self.deleted_bytes, "deleted_records": self.deleted_records}


@dataclass(frozen=True, slots=True)
class _VerifiedEntry:
    record: ProfileInputCacheRecord
    record_path: Path
    content_path: Path

    @property
    def source_key(self) -> tuple[str, str | None, str | None]:
        return (self.record.source.name, self.record.source.set_code, self.record.source.event_format)

    @property
    def sort_key(self) -> tuple[datetime, str, str]:
        return (self.record.acquired_at, self.record.source_version, self.record.sha256)

    @property
    def identity_key(self) -> tuple[Path, str, str]:
        return (self.record_path, self.record.source_version, self.record.sha256)




class ProfileInputCache:
    """Store and verify bounded profile-input objects in a local cache."""

    def __init__(self, root: PathInput, *, policy: ProfileInputCachePolicy, clock: Clock | None = None) -> None:
        try:
            self.root = Path(root)
        except (TypeError, ValueError) as error:
            raise ProfileInputCacheError("Cache root must be a valid local path.") from error
        if not isinstance(policy, ProfileInputCachePolicy):
            raise ProfileInputCacheError("policy must be a ProfileInputCachePolicy.")
        if clock is not None and not callable(clock):
            raise ProfileInputCacheError("clock must be callable.")
        self.policy = policy
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self.objects = self.root / "objects"
        self.records = self.root / "records"

    def store(
        self,
        *,
        source: ProfileInputSource,
        source_version: str,
        input_stream: BinaryIO,
        expected_sha256: str | None = None,
        acquired_at: datetime | None = None,
    ) -> ProfileInputCacheResult:
        """Stream, verify, and atomically publish one immutable input."""

        source = self._source(source)
        source_version = _normalize_version(source_version)
        expected = _hash(expected_sha256, "expected_sha256") if expected_sha256 is not None else None
        if not hasattr(input_stream, "read") or not callable(input_stream.read):
            raise ProfileInputCacheError("input_stream must be a readable binary stream.")
        acquired = self._now() if acquired_at is None else _timestamp(acquired_at, "acquired_at")
        temporary_name: str | None = None
        hidden_entries: list[_VerifiedEntry] = []
        candidate_backup: Path | None = None
        candidate: _VerifiedEntry | None = None
        object_installed = False
        candidate_published = False
        digest = hashlib.sha256()
        content_bytes = 0
        try:
            self._ensure_owned_directories(create=True)
            self._reconcile_pending()

            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".profile-input.", dir=self.objects, delete=False
                ) as temporary:
                    temporary_name = temporary.name
                    while True:
                        chunk = input_stream.read(_CHUNK_SIZE)
                        if chunk == b"":
                            break
                        if not isinstance(chunk, bytes):
                            raise ProfileInputCacheError("input_stream must yield bytes.")
                        content_bytes += len(chunk)
                        if content_bytes > self.policy.max_entry_bytes:
                            raise ProfileInputCacheCapacityError("profile-input entry exceeds max_entry_bytes.")
                        digest.update(chunk)
                        temporary.write(chunk)
                    temporary.flush()
                    os.fsync(temporary.fileno())
            except ProfileInputCacheError:
                raise
            except Exception as error:
                raise ProfileInputCacheError("Could not stage profile-input bytes.") from error

            sha256 = digest.hexdigest()
            if expected is not None and expected != sha256:
                raise ProfileInputCacheError("profile-input checksum does not match expected_sha256.")
            candidate = _VerifiedEntry(
                record=ProfileInputCacheRecord(
                    schema_version=PROFILE_INPUT_CACHE_SCHEMA_VERSION,
                    source=source,
                    source_version=source_version,
                    acquired_at=acquired,
                    sha256=sha256,
                    content_bytes=content_bytes,
                ),
                record_path=self._record_path(source=source, source_version=source_version),
                content_path=self._object_path(sha256),
            )

            existing_metadata = self._record_metadata(
                candidate.record_path,
                source=source,
                source_version=source_version,
            )
            if existing_metadata is not None and (
                existing_metadata.sha256 != sha256 or existing_metadata.content_bytes != content_bytes
            ):
                raise ProfileInputCacheConflictError("source/version is already pinned to different bytes.")
            existing, _ = self._read_verified(candidate.record_path, source=source, source_version=source_version)
            if existing is not None:
                return self._result_for_record(existing.record, existing.content_path, requested_version=source_version)

            entries = self._verified_entries()
            victims = self._retention_victims([*entries, candidate])
            if candidate.identity_key in {entry.identity_key for entry in victims}:
                raise ProfileInputCacheCapacityError("cache bounds cannot retain the new profile-input entry.")

            try:
                hidden_entries = self._reserve_victim_records(victims)
                candidate_backup = self._reserve_existing_record(candidate.record_path)
                destination = candidate.content_path
                if not self._verify_object(destination, sha256=sha256, content_bytes=content_bytes):
                    os.replace(temporary_name, destination)
                    object_installed = True
                    _fsync_directory(destination.parent)
                    temporary_name = None
                else:
                    _unlink(Path(temporary_name) if temporary_name is not None else None)
                    temporary_name = None
                _atomic_write_bytes(candidate.record_path, candidate.record.to_bytes())
                candidate_published = True
            except ProfileInputCacheError:
                raise
            except Exception as error:
                raise ProfileInputCacheError("Could not publish profile-input cache entry.") from error

            self._delete_entries(hidden_entries)
            hidden_entries = []
            if candidate_backup is not None:
                self._delete_paths({candidate_backup})
            assert candidate is not None
            return self._result_for_record(candidate.record, candidate.content_path, requested_version=source_version)
        except BaseException:
            if not candidate_published:
                self._rollback_store(
                    candidate=candidate,
                    hidden_entries=hidden_entries,
                    candidate_backup=candidate_backup,
                    object_installed=object_installed,
                )
            raise
        finally:
            _unlink(Path(temporary_name) if temporary_name is not None else None)

    def lookup(
        self,
        *,
        source: ProfileInputSource,
        source_version: str | None = None,
        expected_sha256: str | None = None,
        offline: bool = False,
    ) -> ProfileInputCacheResult:
        """Return a verified exact or newest profile-input cache entry."""

        source = self._source(source)
        version = _normalize_version(source_version) if source_version is not None else None
        expected = _hash(expected_sha256, "expected_sha256") if expected_sha256 is not None else None
        self._ensure_owned_directories()
        self._reconcile_pending()
        if version is not None:
            path = self._record_path(source=source, source_version=version)
            if not path.exists() and not path.is_symlink():
                return self._result(source, version, ProfileInputCacheOutcome.MISSING)
            entry, diagnostics = self._read_verified(path, source=source, source_version=version, expected=expected)
            if entry is None:
                return self._result(source, version, ProfileInputCacheOutcome.CORRUPT, diagnostics=diagnostics)
            return self._result_for_record(entry.record, entry.content_path, offline=offline, requested_version=version)

        metadata: list[tuple[Path, ProfileInputCacheRecord]] = []
        rejected: list[str] = []
        for path in self._record_files():
            record, diagnostics = self._read_metadata(path, source=source)
            if record is not None:
                metadata.append((path, record))
            elif diagnostics:
                rejected.extend(diagnostics)
        metadata.sort(key=lambda item: (item[1].acquired_at, item[1].source_version, item[1].sha256), reverse=True)
        if not metadata:
            outcome = ProfileInputCacheOutcome.CORRUPT if rejected else ProfileInputCacheOutcome.MISSING
            return self._result(source, None, outcome, diagnostics=tuple(dict.fromkeys(rejected)))

        for index, (path, record) in enumerate(metadata):
            diagnostics = list(rejected)
            if expected is not None and record.sha256 != expected:
                diagnostics.append("content-pin-mismatch")
            content_path = self._object_path(record.sha256)
            if not self._verify_object(content_path, sha256=record.sha256, content_bytes=record.content_bytes):
                diagnostics.append("content-invalid")
            if diagnostics != rejected:
                rejected = diagnostics
                if not offline and index == 0:
                    return self._result(
                        source,
                        None,
                        ProfileInputCacheOutcome.CORRUPT,
                        diagnostics=tuple(dict.fromkeys(rejected)),
                    )
                continue
            return self._result_for_record(
                record,
                content_path,
                offline=offline,
                diagnostics=tuple(dict.fromkeys(rejected)),
            )

        return self._result(
            source,
            None,
            ProfileInputCacheOutcome.CORRUPT,
            diagnostics=tuple(dict.fromkeys(rejected)),
        )

    def prune(self) -> ProfileInputCachePruneResult:
        """Deterministically remove invalid, superseded, and over-capacity data."""
        self._ensure_owned_directories()
        self._reconcile_pending()
        self._delete_temporary_records()

        entries, invalid_records = self._all_entries()
        victims = self._retention_victims(entries)
        invalid_paths = set(invalid_records)
        deleted_invalid_records = self._delete_paths(invalid_paths)
        deleted_victim_records, deleted_bytes = self._delete_entries(victims)
        deleted_records = deleted_invalid_records + deleted_victim_records
        return ProfileInputCachePruneResult(deleted_records=deleted_records, deleted_bytes=deleted_bytes)

    def invalidate(
        self,
        *,
        source: ProfileInputSource,
        source_version: str | None = None,
        allow_offline_loss: bool = False,
    ) -> ProfileInputCachePruneResult:
        """Remove selected entries without silently deleting a source's last copy."""

        source = self._source(source)
        version = _normalize_version(source_version) if source_version is not None else None
        self._ensure_owned_directories()
        self._reconcile_pending()
        entries = self._verified_entries()
        attributable: list[tuple[Path, ProfileInputCacheRecord]] = []
        for path in self._record_files():
            record, _ = self._read_metadata(path, source=source)
            if record is not None and (version is None or record.source_version == version):
                attributable.append((path, record))
        matching_verified = [
            entry
            for entry in entries
            if entry.record.source == source and (version is None or entry.record.source_version == version)
        ]
        if version is not None:
            path = self._record_path(source=source, source_version=version)
            if matching_verified and not allow_offline_loss:
                remaining = [
                    entry
                    for entry in entries
                    if entry.source_key == matching_verified[0].source_key and entry not in matching_verified
                ]
                if not remaining:
                    raise ProfileInputCacheCapacityError("invalidation would remove the last verified offline copy.")
            deleted_records = self._delete_paths({path} if path.exists() or path.is_symlink() else set())
            if deleted_records == 0:
                return ProfileInputCachePruneResult()
            deleted_bytes = self._delete_orphans(
                self._referenced_hashes(),
                only_hashes={record.sha256 for _, record in attributable},
            )
            return ProfileInputCachePruneResult(deleted_records=deleted_records, deleted_bytes=deleted_bytes)

        if matching_verified and not allow_offline_loss:
            remaining = [
                entry
                for entry in entries
                if entry.source_key == matching_verified[0].source_key and entry not in matching_verified
            ]
            if not remaining:
                raise ProfileInputCacheCapacityError("invalidation would remove the last verified offline copy.")
        matching_paths = {path for path, _ in attributable}
        if not matching_paths:
            return ProfileInputCachePruneResult()
        deleted_records = self._delete_paths(matching_paths)
        if deleted_records == 0:
            return ProfileInputCachePruneResult()
        deleted_bytes = self._delete_orphans(
            self._referenced_hashes(),
            only_hashes={record.sha256 for _, record in attributable},
        )
        return ProfileInputCachePruneResult(deleted_records=deleted_records, deleted_bytes=deleted_bytes)



    def _ensure_owned_directories(self, *, create: bool = False) -> None:
        """Reject internal directory links before following or mutating them."""

        if create:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ProfileInputCacheError("Could not create profile-input cache root.") from error
        for path in (self.objects, self.records):
            self._check_owned_directory(path)
            if create and not path.exists():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as error:
                    raise ProfileInputCacheError("Could not create profile-input cache directory.") from error
            self._check_owned_directory(path)

    @staticmethod
    def _check_owned_directory(path: Path) -> None:
        try:
            if path.is_symlink():
                raise ProfileInputCacheError("Profile-input cache directories cannot be symbolic links.")
            if path.exists() and not path.is_dir():
                raise ProfileInputCacheError("Profile-input cache directory is not a directory.")
        except OSError as error:
            raise ProfileInputCacheError("Could not inspect profile-input cache directory.") from error

    def _reconcile_pending(self) -> None:
        pending: set[Path] = set()
        if self.records.is_dir():
            for path in self.records.iterdir():
                if not path.name.startswith(".profile-input-pending."):
                    continue
                if path.is_dir() and not path.is_symlink():
                    raise ProfileInputCacheError("Profile-input cache has an unfinished cleanup.")
                pending.add(path)
        if not pending:
            return
        self._delete_paths(pending)
        self._delete_orphans(self._referenced_hashes())

    def _reserve_victim_records(self, entries: list[_VerifiedEntry]) -> list[_VerifiedEntry]:
        reserved: list[_VerifiedEntry] = []
        try:
            for entry in entries:
                pending_path = self.records / f".profile-input-pending.victim.{entry.record_path.stem}"
                if pending_path.exists() or pending_path.is_symlink():
                    raise ProfileInputCacheError("Profile-input cache has an unfinished cleanup.")
                os.replace(entry.record_path, pending_path)
                reserved.append(
                    _VerifiedEntry(
                        record=entry.record,
                        record_path=pending_path,
                        content_path=entry.content_path,
                    )
                )
            _fsync_directory(self.records)
            return reserved
        except BaseException:
            self._restore_reserved_records(reserved)
            raise

    def _reserve_existing_record(self, path: Path) -> Path | None:
        if not path.exists() and not path.is_symlink():
            return None
        pending_path = self.records / f".profile-input-pending.replaced.{path.stem}"
        if pending_path.exists() or pending_path.is_symlink():
            raise ProfileInputCacheError("Profile-input cache has an unfinished cleanup.")
        try:
            os.replace(path, pending_path)
            _fsync_directory(self.records)
        except OSError as error:
            try:
                if pending_path.exists() or pending_path.is_symlink():
                    os.replace(pending_path, path)
            except OSError:
                pass
            raise ProfileInputCacheError("Could not stage existing profile-input metadata.") from error
        except BaseException:
            try:
                if pending_path.exists() or pending_path.is_symlink():
                    os.replace(pending_path, path)
            except OSError:
                pass
            raise
        return pending_path

    def _restore_reserved_records(self, entries: list[_VerifiedEntry]) -> None:
        for entry in reversed(entries):
            try:
                if entry.record_path.exists() or entry.record_path.is_symlink():
                    os.replace(entry.record_path, self._record_path(
                        source=entry.record.source,
                        source_version=entry.record.source_version,
                    ))
            except OSError:
                pass

    def _rollback_store(
        self,
        *,
        candidate: _VerifiedEntry | None,
        hidden_entries: list[_VerifiedEntry],
        candidate_backup: Path | None,
        object_installed: bool,
    ) -> None:
        if candidate is not None:
            _unlink(candidate.record_path)
            if object_installed:
                _unlink(candidate.content_path)
        if candidate_backup is not None:
            try:
                if candidate_backup.exists() or candidate_backup.is_symlink():
                    os.replace(candidate_backup, candidate.record_path)
            except OSError:
                pass
        self._restore_reserved_records(hidden_entries)

    def _source(self, source: ProfileInputSource) -> ProfileInputSource:
        if not isinstance(source, ProfileInputSource):
            raise ProfileInputCacheError("source must be a ProfileInputSource.")
        return source

    def _now(self) -> datetime:
        try:
            return _timestamp(self._clock(), "cache clock")
        except ProfileInputCacheError:
            raise
        except (TypeError, ValueError) as error:
            raise ProfileInputCacheError("cache clock must return a timezone-aware datetime.") from error

    def _record_path(self, *, source: ProfileInputSource, source_version: str) -> Path:
        identity = _canonical_json_bytes({"source": source.to_json(), "source_version": source_version})
        return self.records / f"{hashlib.sha256(identity).hexdigest()}.json"

    def _object_path(self, sha256: str) -> Path:
        return self.objects / f"{sha256}.bin"

    def _record_files(self) -> tuple[Path, ...]:
        self._check_owned_directory(self.records)
        if not self.records.is_dir():
            return ()
        return tuple(
            sorted(
                (
                    path
                    for path in self.records.iterdir()
                    if path.name.endswith(".json")
                    and not path.name.startswith(".profile-input-pending.")
                    and (path.is_file() or path.is_symlink())
                ),
                key=lambda path: path.name,
            )
        )

    def _read_metadata(
        self,
        path: Path,
        *,
        source: ProfileInputSource | None,
        source_version: str | None = None,
    ) -> tuple[ProfileInputCacheRecord | None, tuple[str, ...]]:
        if path.is_symlink() or not path.is_file():
            return None, ("record-invalid",)
        try:
            record = ProfileInputCacheRecord.from_bytes(path.read_bytes())
        except (OSError, ProfileInputCacheError):
            return None, ("record-invalid",)
        if path != self._record_path(source=record.source, source_version=record.source_version):
            return None, ("record-identity-mismatch",)
        if source is not None and record.source != source:
            return None, ()
        if source_version is not None and record.source_version != source_version:
            return None, ()
        return record, ()

    def _read_verified(
        self,
        path: Path,
        *,
        source: ProfileInputSource | None,
        source_version: str | None = None,
        expected: str | None = None,
    ) -> tuple[_VerifiedEntry | None, tuple[str, ...]]:
        record, metadata_diagnostics = self._read_metadata(
            path,
            source=source,
            source_version=source_version,
        )
        if record is None:
            return None, metadata_diagnostics
        diagnostics: list[str] = []
        if expected is not None and record.sha256 != expected:
            diagnostics.append("content-pin-mismatch")
        object_path = self._object_path(record.sha256)
        if not self._verify_object(object_path, sha256=record.sha256, content_bytes=record.content_bytes):
            diagnostics.append("content-invalid")
        if diagnostics:
            return None, tuple(diagnostics)
        return _VerifiedEntry(record=record, record_path=path, content_path=object_path), ()

    def _record_metadata(
        self,
        path: Path,
        *,
        source: ProfileInputSource,
        source_version: str,
    ) -> ProfileInputCacheRecord | None:
        record, _ = self._read_metadata(path, source=source, source_version=source_version)
        return record
    def _referenced_hashes(self, *, exclude: set[Path] | None = None) -> set[str]:
        excluded = exclude or set()
        referenced: set[str] = set()
        for path in self._record_files():
            if path in excluded:
                continue
            record, _ = self._read_metadata(path, source=None)
            if record is not None:
                referenced.add(record.sha256)
        return referenced



    def _verified_entries(self) -> list[_VerifiedEntry]:
        entries: list[_VerifiedEntry] = []
        for path in self._record_files():
            entry, _ = self._read_verified(path, source=None)
            if entry is not None:
                entries.append(entry)
        return entries


    def _all_entries(self) -> tuple[list[_VerifiedEntry], list[Path]]:
        entries: list[_VerifiedEntry] = []
        invalid: list[Path] = []
        for path in self._record_files():
            entry, _ = self._read_verified(path, source=None)
            if entry is None:
                invalid.append(path)
            else:
                entries.append(entry)
        return entries, invalid

    def _verify_object(self, path: Path, *, sha256: str, content_bytes: int) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            if path.stat().st_size != content_bytes or content_bytes > self.policy.max_entry_bytes:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as content:
                while chunk := content.read(_CHUNK_SIZE):
                    digest.update(chunk)
            return digest.hexdigest() == sha256
        except OSError:
            return False

    def _retention_victims(self, entries: list[_VerifiedEntry]) -> list[_VerifiedEntry]:
        survivors = list(entries)
        victims: list[_VerifiedEntry] = []
        for source_key in sorted({entry.source_key for entry in survivors}, key=lambda key: tuple(value or "" for value in key)):
            source_entries = [entry for entry in survivors if entry.source_key == source_key]
            source_entries.sort(key=lambda entry: entry.sort_key, reverse=True)
            keep = source_entries[: self.policy.max_versions_per_source]
            keep_keys = {entry.identity_key for entry in keep}
            source_victims = [entry for entry in source_entries if entry.identity_key not in keep_keys]
            victims.extend(source_victims)
        survivors = [entry for entry in survivors if entry not in victims]
        mandatory: set[tuple[Path, str, str]] = set()
        for source_key in sorted({entry.source_key for entry in survivors}, key=lambda key: tuple(value or "" for value in key)):
            source_entries = [entry for entry in survivors if entry.source_key == source_key]
            newest = max(source_entries, key=lambda entry: entry.sort_key)
            mandatory.add(newest.identity_key)

        while len(survivors) > self.policy.max_records:
            eligible = [entry for entry in survivors if entry.identity_key not in mandatory]
            if not eligible:
                raise ProfileInputCacheCapacityError("cache max_records cannot preserve one verified entry per source.")
            victim = min(eligible, key=lambda entry: entry.sort_key)
            victims.append(victim)
            survivors.remove(victim)

        def total_bytes(items: list[_VerifiedEntry]) -> int:
            return sum({entry.record.sha256: entry.record.content_bytes for entry in items}.values())

        while total_bytes(survivors) > self.policy.max_total_bytes:
            eligible = [entry for entry in survivors if entry.identity_key not in mandatory]
            if not eligible:
                raise ProfileInputCacheCapacityError("cache max_total_bytes cannot preserve one verified entry per source.")
            victim = min(eligible, key=lambda entry: entry.sort_key)
            victims.append(victim)
            survivors.remove(victim)
        return victims

    def _delete_entries(self, entries: list[_VerifiedEntry]) -> tuple[int, int]:
        paths = {entry.record_path for entry in entries}
        deleted_bytes = self._delete_orphans(self._referenced_hashes(exclude=paths))
        deleted_records = self._delete_paths(paths)
        return deleted_records, deleted_bytes

    def _delete_temporary_records(self) -> None:
        self._check_owned_directory(self.records)
        if not self.records.is_dir():
            return
        for path in sorted(self.records.iterdir(), key=lambda item: item.name):
            if not path.name.startswith(".") or ".json." not in path.name:
                continue
            if path.is_dir() and not path.is_symlink():
                continue
            self._delete_paths({path})

    def _delete_paths(self, paths: set[Path]) -> int:
        deleted = 0
        for path in sorted(paths, key=lambda item: str(item)):
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
            except OSError as error:
                raise ProfileInputCacheError("Could not remove profile-input cache data.") from error
        return deleted

    def _delete_orphans(self, referenced: set[str], *, only_hashes: set[str] | None = None) -> int:
        deleted_bytes = 0
        self._check_owned_directory(self.objects)
        if not self.objects.is_dir():
            return 0
        for path in sorted(self.objects.iterdir(), key=lambda item: item.name):
            if path.is_dir() and not path.is_symlink():
                continue
            if only_hashes is not None and path.stem not in only_hashes:
                continue
            preserve = (
                not path.is_symlink()
                and path.is_file()
                and path.suffix == ".bin"
                and _SHA256_PATTERN.fullmatch(path.stem or "") is not None
                and path.stem in referenced
            )
            if preserve:
                continue
            try:
                size = path.stat().st_size if path.is_file() and not path.is_symlink() else 0
                path.unlink()
                deleted_bytes += size
            except FileNotFoundError:
                pass
            except OSError as error:
                raise ProfileInputCacheError("Could not remove profile-input cache data.") from error
        return deleted_bytes

    def _result(
        self,
        source: ProfileInputSource,
        requested_version: str | None,
        outcome: ProfileInputCacheOutcome,
        *,
        record: ProfileInputCacheRecord | None = None,
        content_path: Path | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> ProfileInputCacheResult:
        return ProfileInputCacheResult(
            source=source,
            requested_version=requested_version,
            outcome=outcome,
            record=record,
            content_path=content_path,
            diagnostics=diagnostics,
        )

    def _result_for_record(
        self,
        record: ProfileInputCacheRecord,
        content_path: Path,
        *,
        offline: bool = False,
        requested_version: str | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> ProfileInputCacheResult:
        if offline:
            outcome = ProfileInputCacheOutcome.OFFLINE_REUSED
        else:
            age = self._now() - record.acquired_at
            outcome = ProfileInputCacheOutcome.STALE if age >= self.policy.freshness_ttl else ProfileInputCacheOutcome.FRESH
        return self._result(
            record.source,
            requested_version,
            outcome,
            record=record,
            content_path=content_path,
            diagnostics=diagnostics,
        )


def _atomic_write_bytes(destination: Path, payload: bytes) -> None:
    try:
        if destination.parent.is_symlink():
            raise ProfileInputCacheError("Profile-input cache directories cannot be symbolic links.")
        if destination.parent.exists() and not destination.parent.is_dir():
            raise ProfileInputCacheError("Profile-input cache directory is not a directory.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.is_symlink():
            raise ProfileInputCacheError("Profile-input cache directories cannot be symbolic links.")
    except OSError as error:
        raise ProfileInputCacheError("Could not create profile-input cache directory.") from error
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        _fsync_directory(destination.parent)
    finally:
        _unlink(Path(temporary_name) if temporary_name is not None else None)



def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)



def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


__all__ = [
    "PROFILE_INPUT_CACHE_SCHEMA_VERSION",
    "ProfileInputCache",
    "ProfileInputCacheCapacityError",
    "ProfileInputCacheConflictError",
    "ProfileInputCacheError",
    "ProfileInputCacheOutcome",
    "ProfileInputCachePolicy",
    "ProfileInputCachePruneResult",
    "ProfileInputCacheRecord",
    "ProfileInputCacheResult",
    "ProfileInputSource",
]
