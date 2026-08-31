from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from draftomen.profile_input_cache import (
    PROFILE_INPUT_CACHE_SCHEMA_VERSION,
    ProfileInputCache,
    ProfileInputCacheCapacityError,
    ProfileInputCacheConflictError,
    ProfileInputCacheError,
    ProfileInputCacheOutcome,
    ProfileInputCachePolicy,
    ProfileInputCacheRecord,
    ProfileInputSource,
)


NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


class FrozenClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def policy() -> ProfileInputCachePolicy:
    return ProfileInputCachePolicy(
        freshness_ttl=timedelta(hours=1),
        max_entry_bytes=1_000,
        max_total_bytes=3_000,
        max_records=10,
        max_versions_per_source=3,
    )


def make_cache(tmp_path: Path, policy: ProfileInputCachePolicy, clock: FrozenClock | None = None) -> ProfileInputCache:
    return ProfileInputCache(tmp_path / "cache", policy=policy, clock=clock or FrozenClock())


def source(name: str = "ratings") -> ProfileInputSource:
    return ProfileInputSource(name, set_code=" tst ", event_format=" QuickDraft ")


def store(cache: ProfileInputCache, payload: bytes, *, version: str = "v1", acquired_at: datetime = NOW):
    return cache.store(
        source=source(),
        source_version=version,
        input_stream=BytesIO(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        acquired_at=acquired_at,
    )


def test_store_writes_canonical_metadata_and_fresh_lookup(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    result = store(cache, b"profile-input")

    assert result.outcome is ProfileInputCacheOutcome.FRESH
    assert result.record is not None
    assert result.record.content_bytes == len(b"profile-input")
    assert result.content_path is not None
    assert result.content_path.read_bytes() == b"profile-input"
    sidecars = tuple((cache.root / "records").glob("*.json"))
    assert len(sidecars) == 1
    payload = sidecars[0].read_bytes()
    assert payload == result.record.to_bytes()
    assert ProfileInputCacheRecord.from_bytes(payload) == result.record
    assert str(cache.root) not in repr(result)
    assert str(cache.root) not in json.dumps(result.to_json())

    result_json = result.to_json()
    assert result_json["cache_outcome"] == "fresh"
    assert result_json["requested_version"] == "v1"
    assert result_json["source_version"] == "v1"
    assert "content_path" not in result_json
    assert "record" not in result_json

def test_freshness_boundary_and_offline_reuse(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    clock = FrozenClock()
    cache = make_cache(tmp_path, policy, clock)
    store(cache, b"bytes")

    clock.value = NOW + policy.freshness_ttl - timedelta(microseconds=1)
    assert cache.lookup(source=source()).outcome is ProfileInputCacheOutcome.FRESH
    clock.value = NOW + policy.freshness_ttl
    assert cache.lookup(source=source()).outcome is ProfileInputCacheOutcome.STALE
    offline = cache.lookup(source=source(), offline=True)
    assert offline.outcome is ProfileInputCacheOutcome.OFFLINE_REUSED


def test_exact_and_latest_missing(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    assert cache.lookup(source=source(), source_version="none").outcome is ProfileInputCacheOutcome.MISSING
    assert cache.lookup(source=source()).outcome is ProfileInputCacheOutcome.MISSING


def test_source_normalization_and_portable_restrictions() -> None:
    assert source() == ProfileInputSource("RATINGS", set_code="TST", event_format="quickdraft")
    for value in ("../secret", "a/b", "a\\b", "a\x00b", "a:b"):
        with pytest.raises(ProfileInputCacheError):
            ProfileInputSource(value)


def test_policy_and_record_are_strict_and_canonical() -> None:
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCachePolicy(timedelta(0), 1, 1, 1, 1)
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCachePolicy(timedelta(seconds=1), 2, 1, 1, 1)
    record = ProfileInputCacheRecord(
        schema_version=PROFILE_INPUT_CACHE_SCHEMA_VERSION,
        source=source(),
        source_version="v1",
        acquired_at=NOW,
        sha256=hashlib.sha256(b"x").hexdigest(),
        content_bytes=1,
    )
    assert ProfileInputCacheRecord.from_bytes(record.to_bytes()) == record
    value = record.to_json()
    value["future"] = True
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCacheRecord.from_json(value)
    value = record.to_json()
    value["schema_version"] = PROFILE_INPUT_CACHE_SCHEMA_VERSION + 1
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCacheRecord.from_json(value)
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCacheRecord.from_bytes(b'{"schema_version":1}')
    noncanonical = json.dumps(record.to_json(), indent=2).encode()
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCacheRecord.from_bytes(noncanonical)


def test_tampered_truncated_and_pin_mismatch_are_corrupt(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"profile-input")
    content_path = next((cache.root / "objects").glob("*.bin"))
    content_path.write_bytes(b"truncated")
    assert cache.lookup(source=source(), source_version="v1").outcome is ProfileInputCacheOutcome.CORRUPT
    content_path.write_bytes(b"profile-input")
    wrong = "0" * 64
    result = cache.lookup(source=source(), source_version="v1", expected_sha256=wrong)
    assert result.outcome is ProfileInputCacheOutcome.CORRUPT
    assert result.content_path is None


def test_identity_mismatch_and_malformed_metadata_are_corrupt(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    result = store(cache, b"profile-input")
    assert result.record is not None
    sidecar = next((cache.root / "records").glob("*.json"))
    value = result.record.to_json()
    value["source_version"] = "different"
    sidecar.write_bytes(ProfileInputCacheRecord.from_json(value).to_bytes())
    assert cache.lookup(source=source(), source_version="v1").outcome is ProfileInputCacheOutcome.CORRUPT
    sidecar.write_bytes(b"not json")
    assert cache.lookup(source=source()).outcome is ProfileInputCacheOutcome.CORRUPT


def test_failed_stream_and_oversize_leave_no_temp_publication(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    with pytest.raises(ProfileInputCacheCapacityError):
        store(cache, b"x" * (policy.max_entry_bytes + 1))
    assert not tuple((cache.root / "records").glob("*.json")) if (cache.root / "records").exists() else True

    class BrokenStream:
        def read(self, size: int) -> bytes:
            raise OSError("stream failed")

    with pytest.raises(ProfileInputCacheError):
        cache.store(source=source(), source_version="v1", input_stream=BrokenStream())
    assert not tuple((cache.root / "objects").glob(".*"))


def test_replace_failure_preserves_prior_record(tmp_path: Path, policy: ProfileInputCachePolicy, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"old")
    original = next((cache.root / "records").glob("*.json")).read_bytes()
    real_replace = __import__("draftomen.profile_input_cache", fromlist=["os"]).os.replace

    def fail_replace(source_path: str, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("draftomen.profile_input_cache.os.replace", fail_replace)
    with pytest.raises(ProfileInputCacheError):
        store(cache, b"new", version="v2")
    monkeypatch.setattr("draftomen.profile_input_cache.os.replace", real_replace)
    assert next((cache.root / "records").glob("*.json")).read_bytes() == original


def test_same_version_deduplicates_and_conflicting_bytes_raise(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    first = store(cache, b"same")
    second = store(cache, b"same", acquired_at=NOW + timedelta(minutes=1))
    assert first.record == second.record
    assert len(tuple((cache.root / "objects").glob("*.bin"))) == 1
    next((cache.root / "objects").glob("*.bin")).write_bytes(b"corrupt")
    with pytest.raises(ProfileInputCacheConflictError):
        store(cache, b"different")


def test_per_source_eviction_and_shared_object_retention(tmp_path: Path) -> None:
    policy = ProfileInputCachePolicy(timedelta(hours=1), 100, 100, 10, 2)
    cache = make_cache(tmp_path, policy)
    store(cache, b"one", version="v1", acquired_at=NOW)
    store(cache, b"two", version="v2", acquired_at=NOW + timedelta(seconds=1))
    store(cache, b"three", version="v3", acquired_at=NOW + timedelta(seconds=2))
    assert cache.lookup(source=source(), source_version="v1").outcome is ProfileInputCacheOutcome.MISSING
    assert cache.lookup(source=source(), source_version="v3").outcome is ProfileInputCacheOutcome.FRESH

    shared = ProfileInputSource("shared")
    cache.store(source=shared, source_version="one", input_stream=BytesIO(b"same"), acquired_at=NOW)
    cache.store(source=shared, source_version="two", input_stream=BytesIO(b"same"), acquired_at=NOW + timedelta(seconds=1))
    cache.invalidate(source=shared, source_version="one")
    assert (cache.root / "objects" / (hashlib.sha256(b"same").hexdigest() + ".bin")).exists()


def test_capacity_refusal_preserves_other_source_last_copy(tmp_path: Path) -> None:
    policy = ProfileInputCachePolicy(timedelta(hours=1), 4, 4, 10, 2)
    cache = make_cache(tmp_path, policy)
    first = ProfileInputSource("first")
    second = ProfileInputSource("second")
    cache.store(source=first, source_version="v1", input_stream=BytesIO(b"1111"), acquired_at=NOW)
    with pytest.raises(ProfileInputCacheCapacityError):
        cache.store(source=second, source_version="v1", input_stream=BytesIO(b"2222"), acquired_at=NOW)
    assert not tuple(cache.objects.glob(".profile-input.*"))
    assert cache.lookup(source=first, offline=True).outcome is ProfileInputCacheOutcome.OFFLINE_REUSED
    assert cache.lookup(source=second).outcome is ProfileInputCacheOutcome.MISSING


def test_guarded_invalidation_and_explicit_offline_loss(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"one")
    with pytest.raises(ProfileInputCacheCapacityError):
        cache.invalidate(source=source(), source_version="v1")
    deleted = cache.invalidate(source=source(), source_version="v1", allow_offline_loss=True)
    assert deleted.deleted_records == 1
    assert deleted.deleted_bytes == len(b"one")
    assert cache.lookup(source=source()).outcome is ProfileInputCacheOutcome.MISSING


def test_offline_latest_falls_back_past_corrupt_newer_record(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"old", version="v1", acquired_at=NOW)
    store(cache, b"new", version="v2", acquired_at=NOW + timedelta(minutes=1))
    newest = cache.lookup(source=source(), source_version="v2")
    assert newest.content_path is not None
    newest.content_path.write_bytes(b"tampered")
    offline = cache.lookup(source=source(), offline=True)
    assert offline.outcome is ProfileInputCacheOutcome.OFFLINE_REUSED
    assert offline.record is not None
    assert offline.record.source_version == "v1"
    assert offline.diagnostics
    assert str(cache.root) not in repr(offline)
    assert str(cache.root) not in json.dumps(offline.to_json())


def test_prune_removes_orphans_and_returns_path_free_counts(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"valid")
    cache.objects.mkdir(parents=True, exist_ok=True)
    (cache.objects / ("f" * 64 + ".bin")).write_bytes(b"orphan")
    (cache.objects / ".profile-input-leftover").write_bytes(b"temp")
    result = cache.prune()
    assert result.deleted_records == 0
    assert result.deleted_bytes == len(b"orphan") + len(b"temp")
    assert str(cache.root) not in repr(result)
    assert str(cache.root) not in json.dumps(result.to_json())


def test_internal_directory_symlinks_are_rejected_without_touching_targets(
    tmp_path: Path, policy: ProfileInputCachePolicy
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"keep")

    store_cache = make_cache(tmp_path / "store", policy)
    store_cache.root.mkdir(parents=True)
    store_cache.objects.symlink_to(external, target_is_directory=True)
    with pytest.raises(ProfileInputCacheError):
        store(store_cache, b"new")
    assert sentinel.read_bytes() == b"keep"

    lookup_cache = make_cache(tmp_path / "lookup", policy)
    lookup_cache.root.mkdir(parents=True)
    lookup_cache.records.symlink_to(external, target_is_directory=True)
    with pytest.raises(ProfileInputCacheError):
        lookup_cache.lookup(source=source())
    assert sentinel.read_bytes() == b"keep"

    prune_cache = make_cache(tmp_path / "prune", policy)
    prune_cache.root.mkdir(parents=True)
    prune_cache.records.symlink_to(external, target_is_directory=True)
    with pytest.raises(ProfileInputCacheError):
        prune_cache.prune()
    assert sentinel.read_bytes() == b"keep"

    invalidate_cache = make_cache(tmp_path / "invalidate", policy)
    invalidate_cache.root.mkdir(parents=True)
    invalidate_cache.objects.symlink_to(external, target_is_directory=True)
    with pytest.raises(ProfileInputCacheError):
        invalidate_cache.invalidate(source=source(), source_version="v1", allow_offline_loss=True)
    assert sentinel.read_bytes() == b"keep"


def test_failed_victim_cleanup_is_reconciled_before_same_version_fast_path(
    tmp_path: Path, policy: ProfileInputCachePolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = ProfileInputCachePolicy(
        freshness_ttl=policy.freshness_ttl,
        max_entry_bytes=policy.max_entry_bytes,
        max_total_bytes=policy.max_total_bytes,
        max_records=policy.max_records,
        max_versions_per_source=1,
    )
    cache = make_cache(tmp_path, policy)
    store(cache, b"old", version="v1", acquired_at=NOW)

    real_delete_entries = cache._delete_entries

    def fail_cleanup(entries: list[object]) -> tuple[int, int]:
        raise ProfileInputCacheError("injected victim deletion failure")

    monkeypatch.setattr(cache, "_delete_entries", fail_cleanup)
    with pytest.raises(ProfileInputCacheError):
        store(cache, b"new", version="v2", acquired_at=NOW + timedelta(minutes=1))
    assert len(tuple(cache.records.glob("*.json"))) == 1
    monkeypatch.setattr(cache, "_delete_entries", real_delete_entries)

    retry = store(cache, b"new", version="v2", acquired_at=NOW + timedelta(minutes=2))
    assert retry.record is not None
    assert retry.record.source_version == "v2"
    assert cache.lookup(source=source(), source_version="v1").outcome is ProfileInputCacheOutcome.MISSING
    assert len(tuple(cache.objects.glob("*.bin"))) == 1
    assert not tuple(cache.records.glob(".profile-input-pending.*"))


def test_versionless_lookup_uses_newest_usable_candidate(
    tmp_path: Path, policy: ProfileInputCachePolicy
) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"old", version="v1", acquired_at=NOW)
    old_object = next(cache.objects.glob("*.bin"))
    old_object.write_bytes(b"tampered")
    newest_payload = b"new"
    store(cache, newest_payload, version="v2", acquired_at=NOW + timedelta(minutes=1))

    result = cache.lookup(source=source())
    assert result.outcome is ProfileInputCacheOutcome.FRESH
    assert result.record is not None
    assert result.record.source_version == "v2"
    pinned = cache.lookup(source=source(), expected_sha256=hashlib.sha256(newest_payload).hexdigest())
    assert pinned.outcome is ProfileInputCacheOutcome.FRESH
    assert pinned.record is not None
    assert pinned.record.source_version == "v2"


def test_backdated_candidate_cannot_evict_newer_entry(tmp_path: Path) -> None:
    policy = ProfileInputCachePolicy(timedelta(hours=1), 100, 100, 10, 1)
    cache = make_cache(tmp_path, policy)
    store(cache, b"new", version="v2", acquired_at=NOW)

    with pytest.raises(ProfileInputCacheCapacityError):
        store(cache, b"old", version="v1", acquired_at=NOW - timedelta(minutes=1))
    assert cache.lookup(source=source(), source_version="v2").outcome is ProfileInputCacheOutcome.FRESH
    assert cache.lookup(source=source(), source_version="v1").outcome is ProfileInputCacheOutcome.MISSING


def test_keyboard_interrupt_during_staging_cleans_temporary_object(
    tmp_path: Path, policy: ProfileInputCachePolicy
) -> None:
    cache = make_cache(tmp_path, policy)

    class InterruptingStream:
        def read(self, size: int) -> bytes:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cache.store(source=source(), source_version="v1", input_stream=InterruptingStream())
    assert not tuple(cache.objects.glob(".profile-input.*"))


def test_keyboard_interrupt_during_publication_cleans_temporary_object(
    tmp_path: Path, policy: ProfileInputCachePolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = make_cache(tmp_path, policy)

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("draftomen.profile_input_cache._atomic_write_bytes", interrupt)
    with pytest.raises(KeyboardInterrupt):
        store(cache, b"payload")
    assert not tuple(cache.objects.glob(".profile-input.*"))
    assert not tuple(cache.records.glob("*.json"))


def test_surrogate_components_and_escaped_surrogate_records_are_rejected() -> None:
    with pytest.raises(ProfileInputCacheError):
        ProfileInputSource("\ud800")

    record = ProfileInputCacheRecord(
        schema_version=PROFILE_INPUT_CACHE_SCHEMA_VERSION,
        source=source(),
        source_version="v1",
        acquired_at=NOW,
        sha256=hashlib.sha256(b"x").hexdigest(),
        content_bytes=1,
    )
    value = record.to_json()
    value["source"]["name"] = "\ud800"
    escaped = (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with pytest.raises(ProfileInputCacheError):
        ProfileInputCacheRecord.from_bytes(escaped)


def test_invalidate_removes_corrupt_attributable_records_and_objects(
    tmp_path: Path, policy: ProfileInputCachePolicy
) -> None:
    cache = make_cache(tmp_path, policy)
    store(cache, b"one", version="v1")
    one_object = cache.lookup(source=source(), source_version="v1").content_path
    assert one_object is not None
    one_object.write_bytes(b"corrupt-one")

    exact = cache.invalidate(source=source(), source_version="v1", allow_offline_loss=True)
    assert exact.deleted_records == 1
    assert exact.deleted_bytes == len(b"corrupt-one")
    assert not one_object.exists()

    store(cache, b"two", version="v2")
    store(cache, b"three", version="v3", acquired_at=NOW + timedelta(minutes=1))
    for object_path in cache.objects.glob("*.bin"):
        object_path.write_bytes(b"corrupt-all")
    all_versions = cache.invalidate(source=source(), allow_offline_loss=True)
    assert all_versions.deleted_records == 2
    assert all_versions.deleted_bytes == len(b"corrupt-all") * 2
    assert not tuple(cache.records.glob("*.json"))


def test_invalidate_counts_dangling_record_symlink(tmp_path: Path, policy: ProfileInputCachePolicy) -> None:
    cache = make_cache(tmp_path, policy)
    cache.records.mkdir(parents=True)
    record_path = cache._record_path(source=source(), source_version="v1")
    record_path.symlink_to(tmp_path / "missing-record.json")

    result = cache.invalidate(source=source(), source_version="v1", allow_offline_loss=True)
    assert result.deleted_records == 1
    assert result.deleted_bytes == 0
    assert not record_path.is_symlink()
