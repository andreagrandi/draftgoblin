from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from draftgoblin.carddb import (
    CardDatabaseCacheMissingError,
    build_card_database_from_bulk_file,
    card_database_cache_path,
    load_card_database,
    refresh_card_database,
)
from draftgoblin.cli import main
from draftgoblin.events import DraftCompletedEvent, PackOfferedEvent, PickMadeEvent, parse_events

FIXTURE_LOG_PATH = Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)


def test_scryfall_bulk_sample_builds_fixture_grp_id_lookup() -> None:
    database = build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)

    fixture_grp_ids = _fixture_grp_ids()
    missing_grp_ids = [
        grp_id for grp_id in sorted(fixture_grp_ids) if database.lookup(grp_id=grp_id).unknown
    ]

    assert len(database) == len(fixture_grp_ids)
    assert missing_grp_ids == []

    spider = database.lookup(grp_id=105097)
    assert spider.name == "Fixture Spider"
    assert spider.colors == ("G",)
    assert spider.mana_value == 4.0
    assert spider.rarity == "rare"
    assert spider.types == ("Creature — Spider",)

    split_card = database.lookup(grp_id=104894)
    assert split_card.colors == ("W", "U")
    assert split_card.types == ("Creature — Front // Creature — Back",)


def test_scryfall_bulk_keeps_mana_cost_and_produced_mana(tmp_path: Path) -> None:
    bulk_path = tmp_path / "mana-fields.jsonl"
    bulk_path.write_text(
        "".join(
            (
                '{"arena_id":1,"name":"Pip Spell","colors":["W"],'
                '"cmc":2,"rarity":"common","type_line":"Creature",'
                '"mana_cost":"{W}{W}"}\n',
                '{"arena_id":2,"name":"Dual Land","colors":[],'
                '"cmc":0,"rarity":"common","type_line":"Land",'
                '"produced_mana":["W","U"]}\n',
                '{"arena_id":3,"name":"Colorless Land","colors":[],'
                '"cmc":0,"rarity":"common","type_line":"Land",'
                '"produced_mana":["C"]}\n',
                '{"arena_id":4,"name":"Hybrid Fixer","colors":[],'
                '"cmc":0,"rarity":"common","type_line":"Land",'
                '"produced_mana":["C","G"]}\n',
            )
        ),
        encoding="utf-8",
    )

    database = build_card_database_from_bulk_file(path=bulk_path)

    assert database.lookup(grp_id=1).mana_cost == "{W}{W}"
    assert database.lookup(grp_id=2).produced_mana == ("W", "U")
    assert database.lookup(grp_id=3).produced_mana == ()
    assert database.lookup(grp_id=4).produced_mana == ("G",)



def test_unknown_grp_id_returns_explicit_marker() -> None:
    database = build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)

    unknown = database.lookup(grp_id=999999)

    assert unknown.unknown is True
    assert unknown.name == "Unknown card 999999"
    assert unknown.colors == ()
    assert unknown.mana_value is None
    assert unknown.rarity == "unknown"
    assert unknown.types == ("Unknown",)


def test_refresh_writes_cache_and_loads_cached_database_offline(
    tmp_path: Path,
) -> None:
    database = refresh_card_database(app_dir=tmp_path, bulk_file=SCRYFALL_BULK_SAMPLE_PATH)

    cache_path = card_database_cache_path(app_dir=tmp_path)
    loaded = load_card_database(app_dir=tmp_path)

    assert cache_path.exists()
    assert loaded.lookup(grp_id=105097) == database.lookup(grp_id=105097)


def test_load_without_cache_raises_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(CardDatabaseCacheMissingError) as error:
        load_card_database(app_dir=tmp_path)

    assert "Run refresh-data first" in str(error.value)


def test_refresh_data_cli_builds_cache_from_vendored_bulk_sample(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        argv=[
            "refresh-data",
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
            "--app-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "refreshed 137 card records" in captured.out
    assert str(card_database_cache_path(app_dir=tmp_path)) in captured.out
    assert captured.err == ""


def test_scryfall_gzipped_jsonl_bulk_files_are_supported(tmp_path: Path) -> None:
    gzip_path = tmp_path / "scryfall-default-cards-sample.jsonl.gz"
    gzip_path.write_bytes(gzip.compress(SCRYFALL_BULK_SAMPLE_PATH.read_bytes()))

    database = build_card_database_from_bulk_file(path=gzip_path)

    assert database.lookup(grp_id=105182).name == "Fixture Final Pick"


def _fixture_grp_ids() -> set[int]:
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    grp_ids: set[int] = set()
    for event in parse_events(lines=fixture_lines):
        if isinstance(event, PackOfferedEvent):
            grp_ids.update(event.offered_grp_ids)
            grp_ids.update(event.pool_grp_ids)
        elif isinstance(event, PickMadeEvent):
            grp_ids.add(event.chosen_grp_id)
        elif isinstance(event, DraftCompletedEvent):
            grp_ids.update(event.picked_grp_ids)

    return grp_ids

