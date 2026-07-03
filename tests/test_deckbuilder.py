from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import DECK_BUILDER
from draftgoblin.deckbuilder import (
    DeckBuilderError,
    format_build_result,
    load_persisted_pool,
    load_pool_file,
    select_color_pair,
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



def test_format_build_result_reports_chosen_runner_up_gap_and_attribution(tmp_path: Path) -> None:
    config = replace(DECK_BUILDER, target_spell_count=4)
    pool = load_pool_file(path=_write_pool_file(tmp_path), set_code="TST")
    selection = select_color_pair(
        pool_grp_ids=pool.pool_grp_ids,
        card_database=_card_database(),
        ratings_data=_ratings_data(),
        config=config,
    )

    output = format_build_result(pool=pool, selection=selection, config=config)

    assert "Chosen pair:" in output
    assert "Runner-up:" in output
    assert "Score gap:" in output
    assert "Pair scores:" in output
    assert "Card data from 17Lands" in output



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
    return SeventeenLandsData(
        set_code="TST",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code="TST",
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            card_ratings={
                1: _stats(grp_id=1, name="White Bomb", color="W", gih=0.65),
                2: _stats(grp_id=2, name="Blue Bomb", color="U", gih=0.64),
                3: _stats(grp_id=3, name="White Playable", color="W", gih=0.62),
                4: _stats(grp_id=4, name="Blue Playable", color="U", gih=0.61),
                5: _stats(grp_id=5, name="Red Filler", color="R", gih=0.52),
                6: _stats(grp_id=6, name="Black Filler", color="B", gih=0.51),
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



def _card(*, grp_id: int, name: str, colors: tuple[str, ...]) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=colors,
        mana_value=2.0,
        rarity="common",
        types=("Creature",),
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
