from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from draftgoblin.carddb import (
    CardDatabase,
    CardDatabaseCacheMissingError,
    CardMetadataSeed,
    augment_card_database_with_mtgjson_set,
    build_card_database_from_arena_data_dir,
    build_card_database_from_bulk_file,
    card_database_cache_path,
    load_card_database,
    load_or_refresh_card_database,
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


def test_scryfall_bulk_keeps_mana_cost_produced_mana_and_image_uri(
    tmp_path: Path,
) -> None:
    bulk_path = tmp_path / "mana-fields.jsonl"
    bulk_path.write_text(
        "".join(
            (
                '{"arena_id":1,"name":"Pip Spell","colors":["W"],'
                '"cmc":2,"rarity":"common","type_line":"Creature",'
                '"mana_cost":"{W}{W}",'
                '"image_uris":{"normal":"https://cards.example/pip.jpg"}}\n',
                '{"arena_id":2,"name":"Dual Land","colors":[],'
                '"cmc":0,"rarity":"common","type_line":"Land",'
                '"produced_mana":["W","U"]}\n',
                '{"arena_id":3,"name":"Colorless Land","colors":[],'
                '"cmc":0,"rarity":"common","type_line":"Land",'
                '"produced_mana":["C"]}\n',
                '{"arena_id":4,"name":"Hybrid Fixer","colors":[],'
                '"cmc":0,"rarity":"common","type_line":"Land",'
                '"produced_mana":["C","G"]}\n',
                '{"arena_id":5,"name":"Faced Preview","card_faces":[{"colors":["R"],'
                '"type_line":"Creature","image_uris":'
                '{"normal":"https://cards.example/face.jpg"}}],'
                '"cmc":2,"rarity":"common","type_line":"Creature"}\n',
                '{"name":"Bulk Only","colors":["B"],"cmc":1,"rarity":"common",'
                '"type_line":"Creature","image_uris":'
                '{"normal":"https://cards.example/bulk-only.jpg"}}\n',
            )
        ),
        encoding="utf-8",
    )

    database = build_card_database_from_bulk_file(path=bulk_path)

    loaded = CardDatabase.from_json(data=database.to_json())

    assert database.lookup(grp_id=1).mana_cost == "{W}{W}"
    assert database.lookup(grp_id=1).image_uri == "https://cards.example/pip.jpg"
    assert database.image_uri_for_name(name="Pip Spell") == "https://cards.example/pip.jpg"
    assert (
        database.image_uri_for_name(name="Bulk Only")
        == "https://cards.example/bulk-only.jpg"
    )
    assert (
        loaded.image_uri_for_name(name="Faced Preview")
        == "https://cards.example/face.jpg"
    )
    assert database.lookup(grp_id=2).produced_mana == ("W", "U")
    assert database.lookup(grp_id=3).produced_mana == ()
    assert database.lookup(grp_id=4).produced_mana == ("G",)
    assert database.lookup(grp_id=5).image_uri == "https://cards.example/face.jpg"


def test_arena_local_data_builds_current_set_metadata(tmp_path: Path) -> None:
    arena_data_dir = _write_arena_data_dir(directory=tmp_path)

    database = build_card_database_from_arena_data_dir(path=arena_data_dir)

    spider = database.lookup(grp_id=105097)
    assert spider.name == "Arena Spider"
    assert spider.colors == ("G",)
    assert spider.mana_value == 4.0
    assert spider.rarity == "rare"
    assert spider.types == ("Creature — Spider",)
    assert spider.mana_cost == "{3}{G}"

    land = database.lookup(grp_id=105200)
    assert land.name == "Arena Dual"
    assert land.colors == ()
    assert land.rarity == "common"
    assert land.types == ("Land",)
    assert land.produced_mana == ("W", "U")


def test_mtgjson_set_metadata_resolves_grp_ids_from_name_seeds() -> None:
    database = augment_card_database_with_mtgjson_set(
        CardDatabase(cards={}),
        set_code="MSH",
        seeds=(
            CardMetadataSeed(
                grp_id=105097,
                name="Arena Spider",
                colors=("G",),
                rarity="rare",
            ),
        ),
        mtgjson_cards=(
            {
                "name": "Arena Spider",
                "manaValue": 4,
                "manaCost": "{3}{G}",
                "colors": ["G"],
                "producedMana": None,
                "rarity": "rare",
                "type": "Creature — Spider",
                "availability": ["arena"],
            },
            {
                "name": "Arena Forest",
                "manaValue": 0,
                "manaCost": None,
                "colors": [],
                "colorIdentity": ["G"],
                "producedMana": ["G"],
                "rarity": "basic",
                "type": "Basic Land — Forest",
                "availability": ["arena"],
            },
        ),
    )

    spider = database.lookup(grp_id=105097)
    assert spider.name == "Arena Spider"
    assert spider.colors == ("G",)
    assert spider.mana_value == 4.0
    assert spider.rarity == "rare"
    assert spider.types == ("Creature — Spider",)
    assert spider.mana_cost == "{3}{G}"
    assert spider.unknown is False

    forest = database.lookup(grp_id=105098)
    assert forest.name == "Arena Forest"
    assert forest.types == ("Basic Land — Forest",)
    assert forest.produced_mana == ("G",)


def test_cached_scryfall_data_is_augmented_with_arena_local_data(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    arena_data_dir = _write_arena_data_dir(directory=tmp_path)
    bulk_path = tmp_path / "stale-scryfall.jsonl"
    bulk_path.write_text(
        '{"arena_id":105097,"name":"Scryfall Spider","colors":["R"],'
        '"cmc":2,"rarity":"common","type_line":"Creature — Fixture",'
        '"image_uris":{"normal":"https://cards.example/spider.jpg"}}\n',
        encoding="utf-8",
    )
    refresh_card_database(app_dir=app_dir, bulk_file=bulk_path)

    database = load_or_refresh_card_database(
        app_dir=app_dir,
        arena_data_dir=arena_data_dir,
    )

    assert database.lookup(grp_id=105097).name == "Arena Spider"
    assert database.lookup(grp_id=105097).colors == ("G",)
    assert database.lookup(grp_id=105097).image_uri == "https://cards.example/spider.jpg"
    assert database.lookup(grp_id=105200).name == "Arena Dual"
    assert database.unresolved_grp_ids(grp_ids=(105097, 999999, 105200)) == (999999,)


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


def test_load_or_refresh_rebuilds_stale_image_index_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = card_database_cache_path(app_dir=tmp_path)
    cache_path.write_text(
        '{"schema_version":1,"source":"old","generated_at":"old","cards":{}}\n',
        encoding="utf-8",
    )
    refreshed = CardDatabase(
        cards={},
        image_uris_by_name={"red room recruit": "https://cards.example/red.jpg"},
    )

    def fake_refresh_card_database(**kwargs: object) -> CardDatabase:
        return refreshed

    monkeypatch.setattr(
        "draftgoblin.carddb.refresh_card_database",
        fake_refresh_card_database,
    )

    assert load_or_refresh_card_database(app_dir=tmp_path) is refreshed


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


def _write_arena_data_dir(*, directory: Path) -> Path:
    arena_data_dir = directory / "arena-data"
    arena_data_dir.mkdir()
    cards = [
        {
            "grpid": 105097,
            "titleId": 1001,
            "cmc": 4,
            "rarity": 4,
            "cardTypeTextId": 2001,
            "subtypeTextId": 2002,
            "colors": [5],
            "colorIdentity": [5],
            "castingcost": "o3oG",
            "linkedFaces": [],
        },
        {
            "grpid": 105200,
            "titleId": 1002,
            "cmc": 0,
            "rarity": 2,
            "cardTypeTextId": 2003,
            "subtypeTextId": 0,
            "colors": [],
            "colorIdentity": [1, 2],
            "castingcost": "o0",
            "linkedFaces": [],
        },
    ]
    localization = [
        {
            "langkey": "EN",
            "isoCode": "en-US",
            "keys": [
                {"id": 1001, "text": "Arena Spider"},
                {"id": 1002, "text": "Arena Dual"},
                {"id": 2001, "text": "Creature"},
                {"id": 2002, "text": "Spider"},
                {"id": 2003, "text": "Land"},
            ],
        }
    ]
    (arena_data_dir / "data_cards_fixture.mtga").write_text(
        json.dumps(cards),
        encoding="utf-8",
    )
    (arena_data_dir / "data_loc_fixture.mtga").write_text(
        json.dumps(localization),
        encoding="utf-8",
    )
    return arena_data_dir

