from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from draftomen.carddb import CardDatabase, CardInfo
from draftomen.profile_generation import generate_set_profile
from draftomen.profile_input_acquisition import (
    CARD_METADATA_ADAPTER_VERSION,
    CardMetadataAdapter,
    ProfileInputAcquisitionOutcome,
    ProfileInputAcquisitionResult,
    acquire_card_metadata_bundle,
)
from draftomen.profile_input_cache import (
    ProfileInputCache,
    ProfileInputCacheCapacityError,
    ProfileInputCacheOutcome,
    ProfileInputCachePolicy,
)
from draftomen.refresh_plan import PlannedEnvironment
from draftomen.set_profile import ProfileMaturity


NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


class FrozenClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class StubCardDatabaseFetcher:
    def __init__(self, database: CardDatabase) -> None:
        self.database = database
        self.calls: list[tuple[str, int]] = []
        self.error: Exception | None = None

    def __call__(self, *, set_code: str, timeout_seconds: int) -> CardDatabase:
        self.calls.append((set_code, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.database


def _environment(*, event_format: str = "QuickDraft") -> PlannedEnvironment:
    return PlannedEnvironment(
        set_code="TST",
        event_format=event_format,
        lifecycle="active",
        reasons=("manual-selection",),
    )


def _database() -> CardDatabase:
    return CardDatabase(
        cards={
            1: CardInfo(
                grp_id=1,
                name="Requested Card",
                colors=("U",),
                mana_value=2,
                rarity="common",
                types=("Creature",),
                set_code="TST",
            ),
            2: CardInfo(
                grp_id=2,
                name="Other Set Card",
                colors=("R",),
                mana_value=3,
                rarity="common",
                types=("Creature",),
                set_code="OTH",
            ),
        },
        image_uris_by_name={"requested card": "https://images.example.test/card.jpg"},
    )


def _cache(tmp_path: Path, *, clock: FrozenClock) -> ProfileInputCache:
    return ProfileInputCache(
        tmp_path / "profile-input-cache",
        policy=ProfileInputCachePolicy(
            freshness_ttl=timedelta(hours=1),
            max_entry_bytes=100_000,
            max_total_bytes=300_000,
            max_records=10,
            max_versions_per_source=3,
        ),
        clock=clock,
    )


def _adapter(fetcher: StubCardDatabaseFetcher) -> CardMetadataAdapter:
    return CardMetadataAdapter(fetch_database=fetcher, timeout_seconds=17)


def _acquire(
    *,
    cache: ProfileInputCache,
    adapter: CardMetadataAdapter,
    clock: FrozenClock,
    event_format: str = "QuickDraft",
    offline: bool = False,
) -> ProfileInputAcquisitionResult:
    return acquire_card_metadata_bundle(
        environment=_environment(event_format=event_format),
        cache=cache,
        adapter=adapter,
        offline=offline,
        clock=clock,
    )


def test_acquisition_builds_set_scoped_bundle_and_metadata_profile(tmp_path: Path) -> None:
    clock = FrozenClock()
    fetcher = StubCardDatabaseFetcher(_database())
    result = _acquire(
        cache=_cache(tmp_path, clock=clock),
        adapter=_adapter(fetcher),
        clock=clock,
    )

    assert result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.ACQUIRED
    assert result.source.cache_lookup_outcome is ProfileInputCacheOutcome.MISSING
    assert result.source.cache_store_outcome is ProfileInputCacheOutcome.FRESH
    assert result.source.acquired_at == NOW
    assert result.source.card_count == 1
    assert result.source.source_version == (
        f"v{CARD_METADATA_ADAPTER_VERSION}-20260831T120000000000Z-{result.source.sha256}"
    )
    assert fetcher.calls == [("TST", 17)]
    assert result.bundle is not None
    assert tuple(result.bundle.card_database.cards) == (1,)
    assert result.bundle.card_database.image_uris_by_name == {}

    generated = generate_set_profile(
        stage="metadata",
        generated_at=NOW,
        **result.bundle.generator_inputs(),
    )
    assert generated.profile.maturity is ProfileMaturity.METADATA_ONLY
    assert generated.profile.set_code == "tst"
    assert generated.profile.event_format == "quickdraft"

    repeated = _acquire(
        cache=_cache(tmp_path / "repeat", clock=clock),
        adapter=_adapter(StubCardDatabaseFetcher(_database())),
        clock=clock,
    )
    report = result.to_bytes().decode("utf-8")
    assert result.to_bytes() == repeated.to_bytes()
    assert str(tmp_path) not in report
    assert "Requested Card" not in report
    assert "Other Set Card" not in report
    assert "images.example.test" not in report


def test_fresh_cache_is_reused_without_fetching_again(tmp_path: Path) -> None:
    clock = FrozenClock()
    cache = _cache(tmp_path, clock=clock)
    fetcher = StubCardDatabaseFetcher(_database())
    adapter = _adapter(fetcher)

    acquired = _acquire(cache=cache, adapter=adapter, clock=clock)
    cached = _acquire(cache=cache, adapter=adapter, clock=clock)

    assert acquired.source.outcome is ProfileInputAcquisitionOutcome.ACQUIRED
    assert cached.source.outcome is ProfileInputAcquisitionOutcome.CACHED
    assert cached.source.cache_lookup_outcome is ProfileInputCacheOutcome.FRESH
    assert cached.source.cache_store_outcome is None
    assert cached.source.source_version == acquired.source.source_version
    assert fetcher.calls == [("TST", 17)]


def test_offline_acquisition_reuses_verified_metadata_without_fetching(tmp_path: Path) -> None:
    clock = FrozenClock()
    cache = _cache(tmp_path, clock=clock)
    fetcher = StubCardDatabaseFetcher(_database())
    adapter = _adapter(fetcher)
    _acquire(cache=cache, adapter=adapter, clock=clock)
    fetcher.error = RuntimeError("network must not be called")

    result = _acquire(
        cache=cache,
        adapter=adapter,
        offline=True,
        clock=clock,
    )

    assert result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.OFFLINE_REUSED
    assert result.source.cache_lookup_outcome is ProfileInputCacheOutcome.OFFLINE_REUSED
    assert fetcher.calls == [("TST", 17)]


def test_offline_cache_miss_is_explicit_and_does_not_fetch(tmp_path: Path) -> None:
    clock = FrozenClock()
    fetcher = StubCardDatabaseFetcher(_database())
    result = _acquire(
        cache=_cache(tmp_path, clock=clock),
        adapter=_adapter(fetcher),
        offline=True,
        clock=clock,
    )

    assert not result.succeeded
    assert result.bundle is None
    assert result.source.outcome is ProfileInputAcquisitionOutcome.MISSING
    assert result.source.cache_lookup_outcome is ProfileInputCacheOutcome.MISSING
    assert result.skip_reasons == ("card-metadata-cache-missing",)
    assert fetcher.calls == []


def test_stale_metadata_survives_failed_refresh_with_bounded_reason(tmp_path: Path) -> None:
    clock = FrozenClock()
    cache = _cache(tmp_path, clock=clock)
    fetcher = StubCardDatabaseFetcher(_database())
    adapter = _adapter(fetcher)
    acquired = _acquire(cache=cache, adapter=adapter, clock=clock)
    clock.value = NOW + timedelta(hours=2)
    fetcher.error = RuntimeError("token=secret at /private/source")

    result = _acquire(cache=cache, adapter=adapter, clock=clock)

    assert result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.STALE
    assert result.source.cache_lookup_outcome is ProfileInputCacheOutcome.STALE
    assert result.source.source_version == acquired.source.source_version
    assert result.skip_reasons == ("card-metadata-refresh-failed",)
    assert result.source.diagnostics == ("card-metadata-acquisition-failed",)
    serialized = result.to_bytes().decode("utf-8")
    assert "secret" not in serialized
    assert "/private/source" not in serialized


def test_corrupt_cache_and_failed_refresh_return_path_free_failure(tmp_path: Path) -> None:
    clock = FrozenClock()
    cache = _cache(tmp_path, clock=clock)
    fetcher = StubCardDatabaseFetcher(_database())
    adapter = _adapter(fetcher)
    _acquire(cache=cache, adapter=adapter, clock=clock)
    lookup = cache.lookup(source=adapter.source_for(environment=_environment()))
    assert lookup.content_path is not None
    lookup.content_path.write_bytes(b"corrupt")
    fetcher.error = RuntimeError(f"credential at {tmp_path}")

    result = _acquire(cache=cache, adapter=adapter, clock=clock)

    assert not result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.CORRUPT
    assert result.source.cache_lookup_outcome is ProfileInputCacheOutcome.CORRUPT
    assert result.skip_reasons == ("card-metadata-cache-corrupt",)
    serialized = result.to_bytes().decode("utf-8")
    assert str(tmp_path) not in serialized
    assert "credential" not in serialized


def test_unavailable_or_wrong_set_metadata_never_bypasses_cache(tmp_path: Path) -> None:
    clock = FrozenClock()
    fetcher = StubCardDatabaseFetcher(CardDatabase(cards={2: _database().cards[2]}))
    result = _acquire(
        cache=_cache(tmp_path, clock=clock),
        adapter=_adapter(fetcher),
        clock=clock,
    )

    assert not result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.UNAVAILABLE
    assert result.source.cache_lookup_outcome is ProfileInputCacheOutcome.MISSING
    assert result.skip_reasons == ("card-metadata-unavailable",)
    assert result.source.source_version is None
    assert result.source.card_count == 0


def test_cache_store_failure_does_not_return_uncached_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FrozenClock()
    cache = _cache(tmp_path, clock=clock)
    fetcher = StubCardDatabaseFetcher(_database())

    def fail_store(**kwargs: object) -> None:
        del kwargs
        raise ProfileInputCacheCapacityError("injected capacity failure")

    monkeypatch.setattr(cache, "store", fail_store)
    result = _acquire(
        cache=cache,
        adapter=_adapter(fetcher),
        clock=clock,
    )

    assert not result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.UNAVAILABLE
    assert result.source.diagnostics == ("card-metadata-cache-store-failed",)
    assert result.skip_reasons == ("card-metadata-cache-store-failed",)


def test_card_metadata_cache_is_shared_across_formats_for_one_set(tmp_path: Path) -> None:
    clock = FrozenClock()
    cache = _cache(tmp_path, clock=clock)
    fetcher = StubCardDatabaseFetcher(_database())
    adapter = _adapter(fetcher)
    _acquire(cache=cache, adapter=adapter, clock=clock)

    result = _acquire(
        cache=cache,
        adapter=adapter,
        clock=clock,
        event_format="PremierDraft",
    )

    assert result.succeeded
    assert result.source.outcome is ProfileInputAcquisitionOutcome.CACHED
    assert result.source.source.event_format is None
    assert result.bundle is not None
    assert result.bundle.environment.event_format == "premierdraft"
    assert fetcher.calls == [("TST", 17)]
