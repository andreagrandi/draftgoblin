from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from draftomen import corpus as corpus_module
from draftomen.corpus import (
    CorpusError,
    CorpusOfflineError,
    SelectionSpec,
    SourceSpec,
    acquire_sources,
    build_coverage_report,
    build_corpus,
    build_default_source_specs,
    iter_normalized_rows,
    load_normalized_rows,
    normalize_cards,
    select_cards,
    write_normalized_rows,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"

def _representative_sources() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    cards = [
        json.loads(line)
        for line in (FIXTURE_DIR / "corpus-scryfall.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    arena = json.loads((FIXTURE_DIR / "corpus-arena-cards.json").read_text(encoding="utf-8"))
    arena.extend(json.loads((FIXTURE_DIR / "corpus-arena-localization.json").read_text(encoding="utf-8")))
    mtgjson = json.loads((FIXTURE_DIR / "corpus-mtgjson.json").read_text(encoding="utf-8"))["data"]["cards"]
    return cards, arena, mtgjson


def _source_specs() -> tuple[SourceSpec, ...]:
    return (
        SourceSpec(
            name="scryfall-default-cards",
            kind="scryfall",
            path=str(FIXTURE_DIR / "corpus-scryfall.jsonl"),
        ),
        SourceSpec(
            name="arena-cards",
            kind="arena",
            path=str(FIXTURE_DIR / "corpus-arena-cards.json"),
        ),
        SourceSpec(
            name="arena-localization",
            kind="arena",
            path=str(FIXTURE_DIR / "corpus-arena-localization.json"),
        ),
        SourceSpec(
            name="mtgjson-hbl",
            kind="mtgjson",
            path=str(FIXTURE_DIR / "corpus-mtgjson.json"),
        ),
    )


def test_acquisition_locks_checksums_and_supports_offline_rebuild(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    first = acquire_sources(source_specs=_source_specs(), cache_dir=cache_dir)

    assert first.lock_path.exists()
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert all(source["sha256"] for source in manifest["sources"])
    assert all(source["retrieved_at"].endswith("Z") for source in manifest["sources"])
    assert all("attribution" in source and "license" in source for source in manifest["sources"])

    normalized, report, _, selected = build_corpus(
        source_specs=_source_specs(),
        cache_dir=cache_dir,
        output_dir=tmp_path / "first",
        selection=SelectionSpec(mode="explicit", sets=("hbl", "dsk")),
        offline=True,
    )
    normalized_again, report_again, _, _ = build_corpus(
        source_specs=_source_specs(),
        cache_dir=cache_dir,
        output_dir=tmp_path / "second",
        selection=SelectionSpec(mode="explicit", sets=("hbl", "dsk")),
        offline=True,
    )

    assert selected.metadata["mode"] == "explicit"
    assert normalized.read_bytes() == normalized_again.read_bytes()
    assert report.read_bytes() == report_again.read_bytes()


def test_manifest_failure_cannot_publish_a_new_authoritative_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    manifest_path = cache_dir / "sources.manifest.json"
    lock_path = cache_dir / "sources.lock.json"
    original_atomic_write = corpus_module._atomic_write

    def fail_manifest(path: Path, payload: bytes) -> None:
        if path == manifest_path:
            raise OSError("manifest write failed")
        original_atomic_write(path, payload)

    monkeypatch.setattr(corpus_module, "_atomic_write", fail_manifest)
    with pytest.raises(OSError, match="manifest write failed"):
        acquire_sources(source_specs=(_source_specs()[0],), cache_dir=cache_dir)
    assert not lock_path.exists()


def test_unpinned_cache_is_reacquired_and_locked_retrieval_time_is_stable(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    source_path = FIXTURE_DIR / "corpus-arena-cards.json"
    spec = SourceSpec(name="arena", kind="arena", path=str(source_path))
    cache_path = cache_dir / "sources" / spec.cache_filename
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"untrusted stale bytes")

    with pytest.raises(CorpusOfflineError, match="cache miss"):
        acquire_sources(source_specs=(spec,), cache_dir=cache_dir, offline=True)
    assert not (cache_dir / "sources.lock.json").exists()

    first = acquire_sources(source_specs=(spec,), cache_dir=cache_dir)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    retrieved_at = manifest["sources"][0]["retrieved_at"]
    assert cache_path.read_bytes() == source_path.read_bytes()

    second = acquire_sources(source_specs=(spec,), cache_dir=cache_dir)
    second_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    assert second_manifest["sources"][0]["retrieved_at"] == retrieved_at
    offline = acquire_sources(source_specs=(spec,), cache_dir=cache_dir, offline=True)
    assert offline.sources[0].retrieved_at == retrieved_at


def test_scryfall_jsonl_source_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = SourceSpec(
        name="scryfall",
        kind="scryfall",
        path=str(FIXTURE_DIR / "corpus-scryfall.jsonl"),
    )
    source = corpus_module.AcquiredSource(
        spec=spec,
        path=Path(spec.path or ""),
        sha256="0" * 64,
        retrieved_at="2026-01-01T00:00:00Z",
    )
    calls: list[Path] = []

    def fake_jsonl(path: Path):
        calls.append(path)
        yield {"name": "lazy"}

    monkeypatch.setattr(corpus_module, "_jsonl_objects", fake_jsonl)
    scryfall, arena, mtgjson = corpus_module._source_cards((source,))
    assert calls == []
    assert next(scryfall)["name"] == "lazy"
    assert calls == [source.path]
    assert arena == ()
    assert mtgjson == ()


def test_scryfall_discovery_prefers_jsonl_download_uri_and_keeps_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": [
            {
                "type": "default_cards",
                "jsonl_download_uri": "https://data.example/current.jsonl.gz",
                "download_uri": "https://data.example/legacy.json.gz",
            }
        ]
    }
    monkeypatch.setattr(
        corpus_module,
        "_fetch_url",
        lambda *args, **kwargs: (json.dumps(payload).encode(), "etag", "version"),
    )
    spec = SourceSpec(name="scryfall", kind="scryfall", url="https://api.example/bulk-data")
    assert corpus_module._resolve_scryfall_source(spec, timeout_seconds=1)[0] == (
        "https://data.example/current.jsonl.gz"
    )

    payload["data"][0].pop("jsonl_download_uri")
    assert corpus_module._resolve_scryfall_source(spec, timeout_seconds=1)[0] == (
        "https://data.example/legacy.json.gz"
    )


def test_remote_download_streams_chunks_and_cleans_failed_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "bulk.jsonl.gz"
    payload = b"one chunk\n" * 100
    sizes: list[int] = []

    class Response:
        headers = {"ETag": "etag-1", "Last-Modified": "today"}

        def __init__(self, *, fail: bool = False) -> None:
            self.offset = 0
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            sizes.append(size)
            if self.fail:
                raise OSError("read failed")
            if self.offset:
                return b""
            self.offset = len(payload)
            return payload

    monkeypatch.setattr(
        corpus_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: Response(),
    )
    digest, etag, version = corpus_module._download_url("https://example.test/bulk", destination)
    assert destination.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert etag == "etag-1"
    assert version == "today"
    assert sizes == [1024 * 1024, 1024 * 1024]

    monkeypatch.setattr(
        corpus_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: Response(fail=True),
    )
    with pytest.raises(CorpusError, match="Could not download"):
        corpus_module._download_url("https://example.test/failing", tmp_path / "failed.jsonl.gz")
    assert not (tmp_path / "failed.jsonl.gz").exists()
    assert not tuple(tmp_path.glob(".failed.jsonl.gz.*"))


def test_acquisition_rejects_cached_checksum_mismatch(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    acquire_sources(source_specs=_source_specs(), cache_dir=cache_dir)
    cached = next((cache_dir / "sources").glob("scryfall-default-cards.*"))
    cached.write_bytes(b"tampered")

    with pytest.raises(CorpusError, match="Checksum mismatch"):
        acquire_sources(source_specs=_source_specs(), cache_dir=cache_dir, offline=True)


def test_offline_acquisition_reports_cache_miss(tmp_path: Path) -> None:
    with pytest.raises(CorpusOfflineError, match="cache miss"):
        acquire_sources(source_specs=_source_specs(), cache_dir=tmp_path / "missing", offline=True)

def test_build_rejects_incomplete_arena_mapping_inputs(tmp_path: Path) -> None:
    specs = tuple(
        spec for spec in _source_specs() if spec.name != "arena-localization"
    )

    with pytest.raises(CorpusError, match="Arena mapping inputs are incomplete"):
        build_corpus(source_specs=specs, cache_dir=tmp_path / "cache")


def test_current_arena_raw_sqlite_inputs_build_corpus(tmp_path: Path) -> None:
    arena_data_dir = tmp_path / "Raw"
    arena_data_dir.mkdir()
    cards_path = arena_data_dir / "Raw_CardDatabase_fixture.mtga"
    localization_path = arena_data_dir / "Raw_ClientLocalization_fixture.mtga"
    with sqlite3.connect(cards_path) as database:
        database.executescript(
            """
            CREATE TABLE Cards (
                GrpId INT,
                TitleId INT,
                TypeTextId INT,
                SubtypeTextId INT,
                Rarity INT,
                OldSchoolManaText TEXT,
                Colors TEXT,
                ColorIdentity TEXT,
                LinkedFaceGrpIds TEXT
            );
            CREATE TABLE Localizations_enUS (
                LocId INT,
                Formatted INT,
                Loc TEXT
            );
            INSERT INTO Cards VALUES (42, 1001, 2001, 2002, 3, 'o1oR', '4', '4', '');
            INSERT INTO Localizations_enUS VALUES (1001, 1, 'Hoblin Adept');
            INSERT INTO Localizations_enUS VALUES (2001, 1, 'Creature');
            INSERT INTO Localizations_enUS VALUES (2002, 1, 'Goblin Wizard');
            """
        )
    with sqlite3.connect(localization_path) as database:
        database.execute("CREATE TABLE Loc (Key TEXT PRIMARY KEY, enUS TEXT)")

    specs = build_default_source_specs(
        arena_data_dir=arena_data_dir,
        scryfall_file=FIXTURE_DIR / "corpus-scryfall.jsonl",
    )
    assert [Path(spec.path).name for spec in specs if spec.kind == "arena"] == [
        cards_path.name,
        localization_path.name,
    ]

    normalized_path, _, _, _ = build_corpus(
        source_specs=specs,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        selection=SelectionSpec(mode="explicit", sets=("hbl",)),
    )
    row = load_normalized_rows(normalized_path)[0]
    assert row["grp_id"] == 42
    assert row["source_provenance"]["arena"]["name"] == "Hoblin Adept"
    assert not any(
        disagreement["field"] == "produced_mana"
        for disagreement in row["source_disagreements"]
    )


def test_normalization_preserves_faces_arena_identity_and_disagreement() -> None:
    cards, arena, mtgjson = _representative_sources()

    rows = normalize_cards(cards, arena_records=arena, mtgjson_records=mtgjson)
    double_faced = next(row for row in rows if row["set"] == "dsk")
    hoblin = next(row for row in rows if row["set"] == "hbl")

    assert [face["name"] for face in double_faced["faces"]] == ["Daybound", "Nightbound"]
    assert double_faced["faces"][0]["oracle_text"].startswith("When this creature")
    assert hoblin["grp_id"] == hoblin["arena_id"] == 42
    assert any(item["field"] == "oracle_text" for item in hoblin["source_disagreements"])
    assert hoblin["source_provenance"]["arena"]["grp_id"] == 42


def test_normalization_builds_auxiliary_indexes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        {"id": "sf-one", "name": "One", "set": "x", "collector_number": "1"},
        {"id": "sf-two", "name": "Two", "set": "x", "collector_number": "2"},
    ]
    mtg_records = [
        {"uuid": "uuid-one", "name": "One", "identifiers": {"scryfallId": "sf-one"}},
        {"uuid": "uuid-two", "name": "Two", "identifiers": {"scryfallId": "sf-two"}},
    ]

    class OnePass:
        def __init__(self, values: list[dict[str, object]]) -> None:
            self.values = values
            self.consumed = False

        def __iter__(self):
            if self.consumed:
                raise AssertionError("auxiliary iterable was consumed more than once")
            self.consumed = True
            yield from self.values

    calls = 0
    original = corpus_module._arena_records

    def count_arena_records(records):
        nonlocal calls
        calls += 1
        return original(records)

    monkeypatch.setattr(corpus_module, "_arena_records", count_arena_records)
    rows = normalize_cards(cards, mtgjson_records=OnePass(mtg_records))

    assert calls == 1
    assert [row["source_provenance"]["mtgjson"]["uuid"] for row in rows] == [
        "uuid-one",
        "uuid-two",
    ]


def test_normalization_retains_mtgjson_only_arena_identity() -> None:
    card = {
        "id": "sf-only",
        "name": "Arena Identity",
        "set": "x",
        "collector_number": "1",
        "layout": "normal",
    }
    mtgjson = {
        "uuid": "uuid-only",
        "name": "Arena Identity",
        "setCode": "x",
        "number": "1",
        "identifiers": {"scryfallId": "sf-only", "mtgArenaId": "987654"},
    }

    row = normalize_cards((card,), mtgjson_records=(mtgjson,))[0]

    assert row["arena_id"] == row["grp_id"] == 987654
    assert row["source_provenance"]["mtgjson"]["mtg_arena_id"] == 987654


def test_mtgjson_printing_and_oracle_matching_fail_closed_on_collisions() -> None:
    exact_card = {
        "id": "sf-print",
        "oracle_id": "oracle-shared",
        "name": "Reprint",
        "set": "set-b",
        "collector_number": "7",
        "layout": "normal",
    }
    mtgjson = (
        {
            "uuid": "exact-printing",
            "name": "Reprint",
            "setCode": "set-a",
            "number": "7",
            "identifiers": {
                "scryfallId": "sf-print",
                "scryfallOracleId": "oracle-shared",
            },
        },
        {
            "uuid": "oracle-set-b",
            "name": "Reprint",
            "setCode": " SET-B ",
            "number": " 7 ",
            "identifiers": {"scryfallOracleId": "oracle-shared"},
        },
    )
    row = normalize_cards((exact_card,), mtgjson_records=mtgjson)[0]
    assert row["source_provenance"]["mtgjson"]["uuid"] == "exact-printing"

    oracle_card = {**exact_card, "id": "missing-printing"}
    row = normalize_cards((oracle_card,), mtgjson_records=mtgjson)[0]
    assert row["source_provenance"]["mtgjson"]["uuid"] == "oracle-set-b"

    ambiguous = (
        {
            "uuid": "ambiguous-one",
            "name": "Reprint",
            "setCode": "set-b",
            "number": "7",
            "identifiers": {"scryfallOracleId": "oracle-shared"},
        },
        {
            "uuid": "ambiguous-two",
            "name": "Reprint",
            "setCode": "set-b",
            "number": "7",
            "identifiers": {"scryfallOracleId": "oracle-shared"},
        },
    )
    arena = ({"grpid": 777, "name": "Reprint"},)
    row = normalize_cards((oracle_card,), arena_records=arena, mtgjson_records=ambiguous)[0]
    assert row["grp_id"] is None
    assert "ambiguous_mtgjson_oracle_identity" in row["unsafe_reasons"]
    assert "mtgjson" not in row["source_provenance"]


def test_mtgjson_type_fills_missing_scryfall_type_line_and_subtypes() -> None:
    card = {
        "id": "sf-type-fallback",
        "name": "Type Fallback",
        "set": "x",
        "collector_number": "1",
        "layout": "normal",
    }
    mtgjson = {
        "uuid": "type-fallback",
        "name": "Type Fallback",
        "setCode": "x",
        "number": "1",
        "type": "Artifact — Equipment",
        "identifiers": {"scryfallId": "sf-type-fallback"},
    }
    row = normalize_cards((card,), mtgjson_records=(mtgjson,))[0]
    assert row["type_line"] == "Artifact — Equipment"
    assert row["subtypes"] == ["Equipment"]


def test_selection_and_coverage_are_inspectable() -> None:
    cards = [json.loads(line) for line in (FIXTURE_DIR / "corpus-scryfall.jsonl").read_text().splitlines()]
    rows = normalize_cards(cards)

    broad = select_cards(rows, SelectionSpec())
    explicit = select_cards(rows, SelectionSpec(mode="explicit", sets=("old",)))
    report = build_coverage_report(rows, selection=broad.metadata)

    assert {row["set"] for row in broad} == {"dsk", "hbl"}
    assert [row["set"] for row in explicit] == ["old"]
    assert report["missing_arena_ids"] == 2
    assert report["missing_semantic_fields"]["oracle_text"] == 1
    assert report["missing_semantic_fields"]["type_line"] == 1
    assert report["unsupported_layouts"] == {"future_layout": 1}
    assert report["wording_mechanic_patterns"] == {
        "adventure": 0,
        "counter": 0,
        "dies": 0,
        "draft_or_conjure": 0,
        "enters_battlefield": 1,
        "exile": 0,
        "modal_choice": 0,
        "sacrifice": 0,
        "transform": 1,
    }
    assert report["source_disagreements"] == 0
    assert report["unsafe_to_classify"] == 1


def test_missing_layout_is_unsupported_and_unsafe() -> None:
    row = normalize_cards(
        (
            {
                "id": "missing-layout",
                "name": "Missing Layout",
                "set": "x",
                "collector_number": "1",
            },
        )
    )[0]
    report = build_coverage_report((row,))
    assert report["unsupported_layouts"] == {"unknown": 1}
    assert report["unsupported_layout_count"] == 1
    assert report["unsafe_to_classify"] == 1
    assert "unsupported_layout:unknown" in row["unsafe_reasons"]


def test_normalized_loader_is_offline_and_accepts_golden_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("normalized loader attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    rows = load_normalized_rows(FIXTURE_DIR / "corpus-golden.jsonl")

    assert len(rows) == 2
    assert rows[0]["layout"] == "transform"
    assert [row["name"] for row in iter_normalized_rows(FIXTURE_DIR / "corpus-golden.jsonl")] == [
        "Daybound // Nightbound",
        "Hoblin Adept",
    ]


def test_normalized_writer_is_byte_stable(tmp_path: Path) -> None:
    rows = load_normalized_rows(FIXTURE_DIR / "corpus-golden.jsonl")
    first = write_normalized_rows(reversed(rows), tmp_path / "first.jsonl")
    second = write_normalized_rows(rows, tmp_path / "second.jsonl")

    assert first.read_bytes() == second.read_bytes()


def test_golden_fixture_matches_normalization_of_representative_sources(tmp_path: Path) -> None:
    cards, arena, mtgjson = _representative_sources()
    normalized = normalize_cards(cards, arena_records=arena, mtgjson_records=mtgjson)
    selected = select_cards(normalized, SelectionSpec(mode="explicit", sets=("hbl", "dsk")))
    generated = write_normalized_rows(selected.cards, tmp_path / "normalized.jsonl")

    assert generated.read_bytes() == (FIXTURE_DIR / "corpus-golden.jsonl").read_bytes()
