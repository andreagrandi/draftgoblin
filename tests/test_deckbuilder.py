from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from draftgoblin.carddb import CardDatabase, CardInfo, build_card_database_from_bulk_file
from draftgoblin.config import DECK_BUILDER
from draftgoblin.deckbuilder import (
    DeckBuilderError,
    ManaBase,
    build_deck_from_pool,
    format_build_result,
    load_persisted_pool,
    load_pool_file,
    select_build_sheet,
    select_color_pair,
    select_deck_spells,
    select_mana_base,
)
from draftgoblin.pool import DraftState, save_draft_state
from draftgoblin.seventeen import (
    QUICK_DRAFT_FORMAT,
    ColorPairWinRate,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
)

FIXTURE_NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC).isoformat()
FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = Path(__file__).parent / "golden"


def test_pair_selection_scores_pool_after_draft_and_reports_gap() -> None:
    config = replace(
        DECK_BUILDER,
        target_spell_count=4,
        pair_score_card_weight=1.0,
        pair_score_win_rate_weight=0.0,
    )

    selection = select_color_pair(
        pool_grp_ids=(1, 2, 3, 4, 5, 6),
        card_database=_card_database(),
        ratings_data=_ratings_data(),
        config=config,
    )

    assert selection.chosen.pair == "WU"
    assert selection.runner_up.pair != "WU"
    assert selection.score_gap > 0
    assert selection.chosen.playable_count == 4
    assert selection.chosen.playable_score_sum > selection.runner_up.playable_score_sum



def test_pair_selection_can_be_forced_without_hiding_automatic_best() -> None:
    config = replace(
        DECK_BUILDER,
        target_spell_count=4,
        pair_score_card_weight=1.0,
        pair_score_win_rate_weight=0.0,
    )

    selection = select_color_pair(
        pool_grp_ids=(1, 2, 3, 4, 5, 6),
        card_database=_card_database(),
        ratings_data=_ratings_data(),
        forced_pair="BR",
        config=config,
    )

    assert selection.chosen.pair == "BR"
    assert selection.automatic.pair == "WU"
    assert selection.score_gap < 0



def test_pair_win_rate_blend_uses_configured_weights() -> None:
    config = replace(
        DECK_BUILDER,
        target_spell_count=4,
        pair_score_card_weight=0.0,
        pair_score_win_rate_weight=1.0,
    )

    selection = select_color_pair(
        pool_grp_ids=(1, 2, 3, 4),
        card_database=_card_database(),
        ratings_data=_ratings_data(),
        config=config,
    )

    assert selection.chosen.pair == "BR"
    assert selection.chosen.pair_win_rate == 0.7



def test_pair_selection_is_deterministic_across_runs() -> None:
    config = replace(DECK_BUILDER, target_spell_count=4)
    kwargs = {
        "pool_grp_ids": (1, 2, 3, 4, 5, 6),
        "card_database": _card_database(),
        "ratings_data": _ratings_data(),
        "config": config,
    }

    first = select_color_pair(**kwargs)
    second = select_color_pair(**kwargs)

    assert first == second
    assert [score.pair for score in first.ranked_scores] == [
        score.pair for score in second.ranked_scores
    ]



def test_select_deck_spells_fills_exact_23_with_structural_constraints() -> None:
    selection = select_deck_spells(
        pool_grp_ids=_constrained_pool_ids(),
        card_database=_constrained_card_database(),
        pair="WU",
    )

    assert selection.counts.total == DECK_BUILDER.target_spell_count
    assert DECK_BUILDER.creature_floor <= selection.counts.creatures
    assert selection.counts.creatures <= DECK_BUILDER.creature_ceiling
    assert selection.counts.two_drops >= DECK_BUILDER.minimum_two_drops
    assert selection.counts.expensive <= DECK_BUILDER.maximum_expensive_spells
    assert selection.applied_relaxations == ()



def test_build_sheet_reselects_aggressive_spell_count_to_keep_40_cards() -> None:
    database = _curve_card_database(mana_value=2.0, count=30)

    build_sheet = select_build_sheet(
        pool_grp_ids=tuple(range(401, 431)),
        card_database=database,
        pair="WU",
    )

    assert build_sheet.spell_selection.counts.total == 24
    assert build_sheet.mana_base.land_count == DECK_BUILDER.aggressive_land_count
    assert build_sheet.mana_base.total_cards == DECK_BUILDER.deck_size



def test_build_sheet_reselects_top_heavy_spell_count_to_keep_40_cards() -> None:
    database = _curve_card_database(mana_value=4.0, count=30)

    build_sheet = select_build_sheet(
        pool_grp_ids=tuple(range(401, 431)),
        card_database=database,
        pair="WU",
    )

    assert build_sheet.spell_selection.counts.total == 22
    assert build_sheet.mana_base.land_count == DECK_BUILDER.top_heavy_land_count
    assert build_sheet.mana_base.total_cards == DECK_BUILDER.deck_size



def test_mana_base_splits_basics_by_pips_with_source_floor() -> None:
    database = _pip_card_database(white_pips=20, blue_pips=3)
    spell_selection = select_deck_spells(
        pool_grp_ids=tuple(range(501, 524)),
        card_database=database,
        pair="WU",
    )

    mana_base = select_mana_base(
        pool_grp_ids=tuple(range(501, 524)),
        card_database=database,
        pair="WU",
        spell_selection=spell_selection,
    )

    assert _basic_count(mana_base=mana_base, name="Plains") == 10
    assert _basic_count(mana_base=mana_base, name="Island") == 7
    assert dict(mana_base.source_counts) == {"W": 10, "U": 7}



def test_mana_base_rounds_toward_double_pip_heavy_color() -> None:
    database = _rounding_card_database()
    spell_selection = select_deck_spells(
        pool_grp_ids=tuple(range(601, 624)),
        card_database=database,
        pair="WU",
    )

    mana_base = select_mana_base(
        pool_grp_ids=tuple(range(601, 624)),
        card_database=database,
        pair="WU",
        spell_selection=spell_selection,
    )

    assert dict(mana_base.pip_counts) == {"W": 9, "U": 9}
    assert dict(mana_base.double_pip_counts) == {"W": 0, "U": 1}
    assert _basic_count(mana_base=mana_base, name="Plains") == 8
    assert _basic_count(mana_base=mana_base, name="Island") == 9



def test_mana_base_slots_in_pair_nonbasics_before_basics() -> None:
    database = _pip_card_database(white_pips=12, blue_pips=12)
    database.cards[524] = _card(
        grp_id=524,
        name="Tranquil Cove",
        colors=(),
        mana_value=0.0,
        types=("Land",),
        produced_mana=("W", "U"),
    )
    spell_selection = select_deck_spells(
        pool_grp_ids=tuple(range(501, 524)) + (524,),
        card_database=database,
        pair="WU",
    )

    mana_base = select_mana_base(
        pool_grp_ids=tuple(range(501, 524)) + (524,),
        card_database=database,
        pair="WU",
        spell_selection=spell_selection,
    )

    assert [land.card.name for land in mana_base.nonbasic_lands] == ["Tranquil Cove"]
    assert sum(basic.count for basic in mana_base.basic_lands) == 16
    assert dict(mana_base.source_counts) == {"W": 9, "U": 9}



def test_near_tie_creature_preference_applies_while_floor_unmet() -> None:
    config = replace(
        DECK_BUILDER,
        target_spell_count=2,
        creature_floor=1,
        creature_ceiling=2,
        minimum_two_drops=0,
        maximum_expensive_spells=2,
        bench_card_count=0,
    )
    database = CardDatabase(
        cards={
            31: _card(
                grp_id=31,
                name="Slightly Better Trick",
                colors=("W",),
                mana_value=3.0,
                types=("Instant",),
            ),
            32: _card(
                grp_id=32,
                name="Near Tie Creature",
                colors=("W",),
                mana_value=3.0,
                types=("Creature — Soldier",),
            ),
            33: _card(
                grp_id=33,
                name="Distant Trick",
                colors=("U",),
                mana_value=3.0,
                types=("Instant",),
            ),
        }
    )
    ratings_data = _ratings_data_from_entries(
        entries=(
            (31, "Slightly Better Trick", "W", 0.560),
            (32, "Near Tie Creature", "W", 0.559),
            (33, "Distant Trick", "U", 0.500),
        )
    )

    selection = select_deck_spells(
        pool_grp_ids=(31, 32, 33),
        card_database=database,
        pair="WU",
        ratings_data=ratings_data,
        config=config,
    )

    assert [spell.card.grp_id for spell in selection.spells] == [32, 31]



def test_spell_selection_lookahead_preserves_overlapping_quotas() -> None:
    config = replace(
        DECK_BUILDER,
        target_spell_count=5,
        creature_floor=2,
        creature_ceiling=2,
        minimum_two_drops=2,
        maximum_expensive_spells=5,
        bench_card_count=0,
    )
    database = CardDatabase(
        cards={
            301: _card(
                grp_id=301,
                name="Three-Drop Creature A",
                colors=("W",),
                mana_value=3.0,
            ),
            302: _card(
                grp_id=302,
                name="Three-Drop Creature B",
                colors=("U",),
                mana_value=3.0,
            ),
            303: _card(
                grp_id=303,
                name="Two-Drop Creature A",
                colors=("W",),
                mana_value=2.0,
            ),
            304: _card(
                grp_id=304,
                name="Two-Drop Creature B",
                colors=("U",),
                mana_value=2.0,
            ),
            305: _card(
                grp_id=305,
                name="Noncreature A",
                colors=("W",),
                mana_value=3.0,
                types=("Instant",),
            ),
            306: _card(
                grp_id=306,
                name="Noncreature B",
                colors=("U",),
                mana_value=3.0,
                types=("Instant",),
            ),
            307: _card(
                grp_id=307,
                name="Noncreature C",
                colors=("W",),
                mana_value=3.0,
                types=("Instant",),
            ),
        }
    )

    selection = select_deck_spells(
        pool_grp_ids=(301, 302, 303, 304, 305, 306, 307),
        card_database=database,
        pair="WU",
        config=config,
    )

    assert [spell.card.grp_id for spell in selection.spells[:2]] == [303, 304]
    assert selection.counts.creatures == 2
    assert selection.counts.two_drops == 2
    assert selection.applied_relaxations == ()



def test_spell_selection_reports_relaxed_two_drop_quota_when_pool_is_short() -> None:
    config = replace(DECK_BUILDER, bench_card_count=0)
    pool_ids = tuple(range(201, 224))
    database = CardDatabase(
        cards={
            grp_id: _card(
                grp_id=grp_id,
                name=f"Fixture Spell {grp_id}",
                colors=("W",) if grp_id % 2 == 0 else ("U",),
                mana_value=2.0 if grp_id < 204 else 3.0,
                types=("Creature — Fixture",) if grp_id < 216 else ("Instant",),
            )
            for grp_id in pool_ids
        }
    )

    selection = select_deck_spells(
        pool_grp_ids=pool_ids,
        card_database=database,
        pair="WU",
        config=config,
    )

    assert selection.counts.total == DECK_BUILDER.target_spell_count
    assert selection.counts.two_drops == 3
    assert "minimum two-drop quota" in selection.applied_relaxations



def test_format_build_result_reports_spell_selection_and_attribution(tmp_path: Path) -> None:
    config = replace(DECK_BUILDER, target_spell_count=4)
    pool = load_pool_file(path=_write_pool_file(tmp_path), set_code="TST")
    selection = select_color_pair(
        pool_grp_ids=pool.pool_grp_ids,
        card_database=_card_database(),
        ratings_data=_ratings_data(),
        config=config,
    )
    build_sheet = select_build_sheet(
        pool_grp_ids=pool.pool_grp_ids,
        card_database=_card_database(),
        pair=selection.chosen.pair,
        ratings_data=_ratings_data(),
        config=config,
    )

    output = format_build_result(
        pool=pool,
        selection=selection,
        spell_selection=build_sheet.spell_selection,
        mana_base=build_sheet.mana_base,
        config=config,
    )

    assert "Chosen pair:" in output
    assert "Runner-up:" in output
    assert "Score gap:" in output
    assert "Pair scores:" in output
    assert "Build sheet:" in output
    assert "Deck size: 40 cards" in output
    assert "Spell selection:" in output
    assert "Selected spells: 4/23" in output
    assert "Lands:" in output
    assert "--allow-splash is inert in v1" in output
    assert "Card data from 17Lands" in output



def test_constrained_build_output_matches_golden_fixture() -> None:
    pool = load_pool_file(path=Path("tests/fixtures/deckbuilder-constrained-pool.json"))
    database = build_card_database_from_bulk_file(
        path=FIXTURES_DIR / "deckbuilder-constrained-bulk.jsonl"
    )
    selection, build_sheet = build_deck_from_pool(
        pool=pool,
        card_database=database,
    )

    output = format_build_result(
        pool=pool,
        selection=selection,
        spell_selection=build_sheet.spell_selection,
        mana_base=build_sheet.mana_base,
    )

    assert output == (GOLDEN_DIR / "deckbuilder-constrained-build.txt").read_text(
        encoding="utf-8"
    )



def test_load_pool_file_supports_state_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text(
        '{"set_code":"tst","account_id":"acct","draft_id":"draft",'
        '"pool_grp_ids":[1,2,3]}',
        encoding="utf-8",
    )

    pool = load_pool_file(path=path)

    assert pool.set_code == "TST"
    assert pool.pool_grp_ids == (1, 2, 3)
    assert pool.account_id == "acct"
    assert pool.draft_id == "draft"



def test_load_pool_file_requires_set_code_for_simple_lists(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(DeckBuilderError, match="--set-code"):
        load_pool_file(path=path)

    pool = load_pool_file(path=path, set_code="tst")
    assert pool.set_code == "TST"
    assert pool.pool_grp_ids == (1, 2, 3)



def test_load_persisted_pool_defaults_to_latest_and_filters(tmp_path: Path) -> None:
    older = _draft_state(
        account_id="acct-a",
        draft_id="draft-a",
        updated_at="2026-07-03T10:00:00+00:00",
    )
    newer = _draft_state(
        account_id="acct-b",
        draft_id="draft-b",
        updated_at="2026-07-03T11:00:00+00:00",
    )
    save_draft_state(state=older, app_dir=tmp_path)
    save_draft_state(state=newer, app_dir=tmp_path)

    latest = load_persisted_pool(app_dir=tmp_path)
    filtered = load_persisted_pool(app_dir=tmp_path, account_id="acct-a")

    assert latest.account_id == "acct-b"
    assert latest.draft_id == "draft-b"
    assert filtered.account_id == "acct-a"
    assert filtered.draft_id == "draft-a"



def _write_pool_file(directory: Path) -> Path:
    path = directory / ".deckbuilder-test-pool.json"
    path.write_text("[1, 2, 3, 4, 5, 6]", encoding="utf-8")
    return path



def _ratings_data() -> SeventeenLandsData:
    return _ratings_data_from_entries(
        entries=(
            (1, "White Bomb", "W", 0.65),
            (2, "Blue Bomb", "U", 0.64),
            (3, "White Playable", "W", 0.62),
            (4, "Blue Playable", "U", 0.61),
            (5, "Red Filler", "R", 0.52),
            (6, "Black Filler", "B", 0.51),
        )
    )



def _ratings_data_from_entries(
    *,
    entries: tuple[tuple[int, str, str, float], ...],
) -> SeventeenLandsData:
    return SeventeenLandsData(
        set_code="TST",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code="TST",
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            card_ratings={
                grp_id: _stats(grp_id=grp_id, name=name, color=color, gih=gih)
                for grp_id, name, color, gih in entries
            },
            pair_win_rates={
                "WU": ColorPairWinRate(pair="WU", wins=60, games=100, win_rate=0.6),
                "BR": ColorPairWinRate(pair="BR", wins=70, games=100, win_rate=0.7),
            },
        ),
        fallback=None,
        thin_sample_minimum=500,
    )



def _stats(*, grp_id: int, name: str, color: str, gih: float) -> SeventeenCardStats:
    return SeventeenCardStats(
        grp_id=grp_id,
        name=name,
        color=color,
        rarity="common",
        average_last_seen_at=4.0,
        gih_win_rate=gih,
        opening_hand_win_rate=None,
        drawn_improvement_win_rate=None,
        sample_counts=RatingSampleCounts(
            seen=1000,
            picked=600,
            games_played=900,
            opening_hand=300,
            games_in_hand=900,
        ),
    )



def _card_database() -> CardDatabase:
    return CardDatabase(
        cards={
            1: _card(grp_id=1, name="White Bomb", colors=("W",)),
            2: _card(grp_id=2, name="Blue Bomb", colors=("U",)),
            3: _card(grp_id=3, name="White Playable", colors=("W",)),
            4: _card(grp_id=4, name="Blue Playable", colors=("U",)),
            5: _card(grp_id=5, name="Red Filler", colors=("R",)),
            6: _card(grp_id=6, name="Black Filler", colors=("B",)),
        }
    )



def _constrained_pool_ids() -> tuple[int, ...]:
    return tuple(range(101, 129))



def _constrained_card_database() -> CardDatabase:
    cards: dict[int, CardInfo] = {}
    for grp_id in range(101, 106):
        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Two-Drop Creature {grp_id}",
            colors=("W",) if grp_id % 2 == 0 else ("U",),
            mana_value=2.0,
            types=("Creature — Fixture",),
        )

    for grp_id in range(106, 115):
        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Mid Curve Creature {grp_id}",
            colors=("W",) if grp_id % 2 == 0 else ("U",),
            mana_value=3.0,
            types=("Creature — Fixture",),
        )

    for grp_id in range(115, 119):
        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Expensive Creature {grp_id}",
            colors=("W",) if grp_id % 2 == 0 else ("U",),
            mana_value=6.0,
            types=("Creature — Fixture",),
        )

    for grp_id in range(119, 129):
        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Noncreature Spell {grp_id}",
            colors=("W",) if grp_id % 2 == 0 else ("U",),
            mana_value=3.0,
            types=("Instant",),
        )

    return CardDatabase(cards=cards)



def _curve_card_database(*, mana_value: float, count: int) -> CardDatabase:
    cards: dict[int, CardInfo] = {}
    for offset in range(count):
        grp_id = 401 + offset
        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Curve Fixture {grp_id}",
            colors=("W",) if offset % 2 == 0 else ("U",),
            mana_value=mana_value,
            types=("Creature — Fixture",) if offset < 18 else ("Instant",),
            mana_cost="{W}" if offset % 2 == 0 else "{U}",
        )

    return CardDatabase(cards=cards)



def _pip_card_database(*, white_pips: int, blue_pips: int) -> CardDatabase:
    cards: dict[int, CardInfo] = {}
    for offset in range(23):
        grp_id = 501 + offset
        if offset < 10:
            colors = ("W",)
            mana_cost = "{W}{W}" if white_pips >= 20 else "{W}"
        elif offset < 13:
            colors = ("U",)
            mana_cost = "{U}"
        else:
            colors = ()
            mana_cost = None

        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Pip Fixture {grp_id}",
            colors=colors,
            mana_value=3.0,
            types=("Creature — Fixture",) if offset < 14 else ("Instant",),
            mana_cost=mana_cost,
        )

    if white_pips == 12:
        cards[501] = _card(
            grp_id=501,
            name="Double White Fixture",
            colors=("W",),
            mana_value=3.0,
            types=("Creature — Fixture",),
            mana_cost="{W}{W}{W}",
        )

    if blue_pips == 12:
        cards[511] = _card(
            grp_id=511,
            name="Double Blue Fixture",
            colors=("U",),
            mana_value=3.0,
            types=("Creature — Fixture",),
            mana_cost="{U}{U}{U}{U}{U}{U}{U}{U}{U}{U}",
        )

    return CardDatabase(cards=cards)



def _rounding_card_database() -> CardDatabase:
    cards: dict[int, CardInfo] = {}
    for offset in range(23):
        grp_id = 601 + offset
        if offset < 9:
            colors = ("W",)
            mana_cost = "{W}"
        elif offset < 16:
            colors = ("U",)
            mana_cost = "{U}"
        elif offset == 16:
            colors = ("U",)
            mana_cost = "{U}{U}"
        else:
            colors = ()
            mana_cost = None

        cards[grp_id] = _card(
            grp_id=grp_id,
            name=f"Rounding Fixture {grp_id}",
            colors=colors,
            mana_value=3.0,
            types=("Creature — Fixture",) if offset < 14 else ("Instant",),
            mana_cost=mana_cost,
        )

    return CardDatabase(cards=cards)



def _basic_count(*, mana_base: ManaBase, name: str) -> int:
    return sum(basic.count for basic in mana_base.basic_lands if basic.name == name)



def _card(
    *,
    grp_id: int,
    name: str,
    colors: tuple[str, ...],
    mana_value: float = 2.0,
    types: tuple[str, ...] = ("Creature",),
    mana_cost: str | None = None,
    produced_mana: tuple[str, ...] = (),
) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=colors,
        mana_value=mana_value,
        rarity="common",
        types=types,
        mana_cost=mana_cost,
        produced_mana=produced_mana,
    )



def _draft_state(*, account_id: str, draft_id: str, updated_at: str) -> DraftState:
    return DraftState(
        account_id=account_id,
        draft_id=draft_id,
        event_name="QuickDraft_TST_20260703",
        set_code="TST",
        course_id=draft_id,
        started_at=FIXTURE_NOW,
        updated_at=updated_at,
        completed_at=None,
        completed=False,
        picks=(),
        pool_grp_ids=(1, 2, 3, 4),
    )
