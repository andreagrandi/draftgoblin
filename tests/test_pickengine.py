from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime

import pytest

from draftomen.carddb import CardDatabase, CardInfo
from draftomen.events import PackOfferedEvent
from draftomen.pickengine import (
    ContextualScoreBreakdown,
    MAX_CONTEXTUAL_ADJUSTMENT,
    MAX_FIXING_TERM,
    MAX_REDUNDANCY_TERM,
    MAX_ROLE_TERM,
    MAX_SYNERGY_TERM,
    MAX_UNSUPPORTED_PAYOFF_TERM,
    MAX_URGENCY_TERM,
    PickEngine,
    PickScoringContext,
    ScoredPack,
    recommendation_confidence_summary,
    recommendation_explanation,
)
from draftomen.pool_ledger import (
    COMPLETED_POOL,
    PoolRoleLedger,
    project_pool_role_ledger,
)
from draftomen.ranking import RANKING_MODES, rank_scored_cards
from draftomen.replay import format_pack_offered_event
from draftomen.set_profile import (
    CardPairSynergy,
    PairProfile,
    ProfileMaturity,
    RoleTarget,
    SampleSummary,
    SetProfile,
    SourceMetadata,
)
from draftomen.semantic_roles import (
    CompiledRoleProfile,
    ProfileCard,
    ProducedResources,
    Role,
    RoleAssignment,
)
from draftomen.seventeen import (
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    ColorPairWinRate,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
)
from draftomen.splash import card_is_castable_in_pair, splash_requirement


def test_pick_engine_scores_and_sorts_with_fallback_sources() -> None:
    engine = PickEngine(ratings_data=_ratings_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=_card_database(),
    )
    assert [card.card.grp_id for card in scored_pack.cards] == [1, 4, 2, 3]
    assert scored_pack.scoring_context is None

    assert [card.score for card in scored_pack.cards] == sorted(
        [card.score for card in scored_pack.cards],
        reverse=True,
    )
    assert scored_pack.cards[0].card.grp_id == 1
    assert {card.card.grp_id: card.source_label for card in scored_pack.cards} == {
        1: "Quick",
        2: "Premier",
        3: "Quick",
        4: "Prior*",
    }
    assert scored_pack.source_summary == "QuickDraft + Premier fallback + neutral prior"
    assert all(0 <= card.score <= 100 for card in scored_pack.cards)
    assert all(isinstance(card.score, int) for card in scored_pack.cards)


def test_set_reliability_calculation_does_not_change_card_scores() -> None:
    ratings_data = _ratings_data()
    database = _card_database()
    before = PickEngine(ratings_data=ratings_data).score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=database,
    )

    reliability = ratings_data.set_reliability

    after = PickEngine(ratings_data=ratings_data).score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=database,
    )
    assert reliability.set_code == ratings_data.set_code
    assert after == before


def test_alsa_adjusts_neutral_prior_when_gih_is_absent() -> None:
    engine = PickEngine(ratings_data=_ratings_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(4, 5, 6),
        card_database=_card_database(),
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert by_id[4].prior_adjusted_by_alsa is True
    assert by_id[5].prior_adjusted_by_alsa is True
    assert by_id[6].prior_adjusted_by_alsa is False
    assert by_id[4].base_rating > engine.normalization.neutral_rating
    assert by_id[5].base_rating < engine.normalization.neutral_rating
    assert by_id[6].score == 50


def test_missing_ratings_data_still_scores_every_card_with_marked_prior() -> None:
    engine = PickEngine()

    scored_pack = engine.score_pack(
        offered_grp_ids=(1, 2, 3),
        card_database=_card_database(),
    )

    assert [card.score for card in scored_pack.cards] == [50, 50, 50]
    assert all(card.source_label == "Prior*" for card in scored_pack.cards)
    assert scored_pack.source_summary == "neutral prior"


def test_freely_available_basic_land_scores_zero_and_ranks_last() -> None:
    scored_pack = PickEngine(ratings_data=_ratings_data()).score_pack(
        offered_grp_ids=(13, 3, 10),
        card_database=_card_database(),
    )

    assert [card.card.name for card in scored_pack.cards] == [
        "Quick Filler",
        "Blue Filler",
        "Arena Plains",
    ]
    assert all(card.score > 0 for card in scored_pack.cards[:-1])
    basic_land = scored_pack.cards[-1]
    assert basic_land.score == 0
    assert basic_land.source_label == "Basic"
    assert basic_land.no_data is False
    assert basic_land.freely_available_basic is True
    assert recommendation_explanation(
        scored_card=basic_land,
        inferred_pair=None,
    ) == (
        "Arena Plains is freely available during deck building, so it receives "
        "0 DO points and ranks after draftable cards."
    )


def test_special_and_nonbasic_lands_retain_normal_scoring() -> None:
    scored_pack = PickEngine().score_pack(
        offered_grp_ids=(14, 15, 16),
        card_database=_card_database(),
    )

    assert {
        card.card.name: (
            card.score,
            card.source_label,
            card.no_data,
            card.freely_available_basic,
        )
        for card in scored_pack.cards
    } == {
        "Wastes": (50, "Prior*", True, False),
        "Snow-Covered Plains": (50, "Prior*", True, False),
        "Prairie Sanctuary": (50, "Prior*", True, False),
    }


def test_canonical_basic_overrides_rating_and_loses_zero_score_tie() -> None:
    scored_pack = PickEngine(ratings_data=_rated_basic_and_zero_data()).score_pack(
        offered_grp_ids=(121, 120),
        card_database=_splash_card_database(),
    )

    assert [card.card.name for card in scored_pack.cards] == [
        "Draftable Zero",
        "Mountain",
    ]
    assert [card.score for card in scored_pack.cards] == [0, 0]
    for ranking_mode in RANKING_MODES:
        assert [
            card.card.name
            for card in rank_scored_cards(
                cards=scored_pack.cards,
                ranking_mode=ranking_mode,
            )
        ] == ["Draftable Zero", "Mountain"]

    mountain = scored_pack.cards[-1]
    assert mountain.rating.gih_win_rate == 0.70
    assert mountain.source_label == "Basic"
    assert mountain.no_data is False
    assert recommendation_confidence_summary(
        cards=scored_pack.cards,
        ranking_mode="score",
        phase="building",
    ) is None


def test_recommendation_confidence_summary_uses_shared_open_pick_copy() -> None:
    scored_pack = PickEngine().score_pack(
        offered_grp_ids=(1,),
        card_database=_card_database(),
    )

    assert recommendation_confidence_summary(
        cards=scored_pack.cards,
        ranking_mode="score",
        phase="open",
    ) == "early/open pick — stay flexible"
    assert recommendation_confidence_summary(
        cards=scored_pack.cards,
        ranking_mode="score",
        phase="building",
    ) is None


def test_commitment_ramp_changes_same_card_score_by_pick_index() -> None:
    engine = PickEngine(ratings_data=_ratings_data())
    database = _card_database()

    early = engine.score_pack(
        offered_grp_ids=(7,),
        card_database=database,
        pool_grp_ids=(1, 2),
        pick_index=5,
    )
    mid = engine.score_pack(
        offered_grp_ids=(7,),
        card_database=database,
        pool_grp_ids=(1, 2),
        pick_index=10,
    )
    late = engine.score_pack(
        offered_grp_ids=(7,),
        card_database=database,
        pool_grp_ids=(1, 2),
        pick_index=16,
    )

    assert early.commitment.inferred_pair == "WU"
    assert early.commitment.level == 0.0
    assert mid.commitment.phase == "building"
    assert late.commitment.phase == "locked"
    assert early.cards[0].color_fit == "open"
    assert mid.cards[0].color_fit == "on-color"
    assert early.cards[0].score < mid.cards[0].score < late.cards[0].score


def test_explicit_global_pick_index_drives_commitment_and_ledger_stage() -> None:
    scored_pack = PickEngine().score_pack(
        offered_grp_ids=(7,),
        card_database=_card_database(),
        pool_grp_ids=(1, 2),
        pack_number=2,
        pick_number=6,
        global_pick_index=35,
        estimated_remaining_picks=7,
    )

    assert scored_pack.commitment.pick_index == 35
    assert scored_pack.commitment.phase == "locked"
    assert scored_pack.role_ledger is not None
    assert scored_pack.role_ledger.global_pick_index == 35


def test_context_stage_index_controls_commitment_and_rejects_conflicts() -> None:
    database = _card_database()
    profile = _context_profile()
    context = PickScoringContext(
        set_profile=profile,
        role_ledger=_context_ledger(profile=profile, database=database),
    )
    engine = PickEngine(scoring_context=context)

    scored_pack = engine.score_pack(
        offered_grp_ids=(7,),
        card_database=database,
        pool_grp_ids=(1, 2),
    )

    assert scored_pack.commitment.pick_index == 35
    assert scored_pack.commitment.phase == "locked"

    with pytest.raises(ValueError, match="conflicts"):
        engine.score_pack(
            offered_grp_ids=(7,),
            card_database=database,
            pool_grp_ids=(1, 2),
            pick_index=34,
        )
    with pytest.raises(ValueError, match="conflicts"):
        engine.score_pack(
            offered_grp_ids=(7,),
            card_database=database,
            pool_grp_ids=(1, 2),
            global_pick_index=34,
        )
    with pytest.raises(ValueError, match="conflict"):
        engine.score_pack(
            offered_grp_ids=(7,),
            card_database=database,
            pool_grp_ids=(1, 2),
            pick_index=34,
            global_pick_index=35,
        )


@pytest.mark.parametrize(
    ("coordinate", "value"),
    [
        ("pack_number", 1),
        ("pick_number", 5),
        ("pick_index", 34),
        ("global_pick_index", 34),
        ("estimated_remaining_picks", 6),
    ],
)
def test_context_rejects_conflicting_stage_coordinates(
    coordinate: str,
    value: int,
) -> None:
    database = _card_database()
    profile = _context_profile()
    context = PickScoringContext(
        set_profile=profile,
        role_ledger=_context_ledger(profile=profile, database=database),
    )

    with pytest.raises(ValueError, match=coordinate):
        PickEngine(scoring_context=context).score_pack(
            offered_grp_ids=(7,),
            card_database=database,
            pool_grp_ids=(1, 2),
            **{coordinate: value},
        )


def test_context_accepts_matching_redundant_stage_coordinates() -> None:
    database = _card_database()
    profile = _context_profile()
    context = PickScoringContext(
        set_profile=profile,
        role_ledger=_context_ledger(profile=profile, database=database),
    )

    scored_pack = PickEngine(scoring_context=context).score_pack(
        offered_grp_ids=(7,),
        card_database=database,
        pool_grp_ids=(1, 2),
        pack_number=2,
        pick_number=6,
        pick_index=35,
        global_pick_index=35,
        estimated_remaining_picks=7,
    )

    assert scored_pack.scoring_context is context
    assert scored_pack.commitment.pick_index == 35


def test_off_color_cards_are_penalized_and_marked_when_committed() -> None:
    engine = PickEngine(ratings_data=_ratings_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(7, 8),
        card_database=_card_database(),
        pool_grp_ids=(1, 2),
        pick_index=16,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert scored_pack.commitment.inferred_pair == "WU"
    assert by_id[7].color_fit == "on-color"
    assert by_id[8].color_fit == "off-color"
    assert by_id[7].score > by_id[8].score


def test_pool_weight_uses_card_quality_when_inferring_pair() -> None:
    engine = PickEngine(ratings_data=_ratings_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(7,),
        card_database=_card_database(),
        pool_grp_ids=(9, 10, 11),
        pick_index=16,
    )

    assert scored_pack.commitment.inferred_pair == "WR"


def test_open_pick_pair_win_rate_tiebreaker_prefers_higher_rate_pair() -> None:
    engine = PickEngine(ratings_data=_msh_pair_tiebreaker_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(31, 32),
        card_database=_msh_pair_tiebreaker_database(),
        pool_grp_ids=(20, 21),
        pick_index=3,
    )
    ranked_cards = rank_scored_cards(cards=scored_pack.cards, ranking_mode="score")
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert scored_pack.commitment.phase == "open"
    assert by_id[31].pair_tiebreaker_pair == "WU"
    assert by_id[31].pair_tiebreaker_win_rate == 0.606
    assert by_id[32].pair_tiebreaker_pair == "BR"
    assert by_id[32].pair_tiebreaker_win_rate == 0.539
    assert by_id[31].raw_score < by_id[32].raw_score
    assert scored_pack.cards[0].card.grp_id == 31
    assert ranked_cards[0].card.grp_id == 31


def test_pair_win_rate_tiebreaker_does_not_override_colorless_card_score() -> None:
    engine = PickEngine(ratings_data=_msh_pair_tiebreaker_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(31, 33),
        card_database=_msh_pair_tiebreaker_database(),
        pool_grp_ids=(20, 21),
        pick_index=3,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert by_id[33].pair_tiebreaker_win_rate is None
    assert by_id[33].raw_score > by_id[31].raw_score
    assert scored_pack.cards[0].card.grp_id == 33


def test_pair_win_rate_tiebreaker_does_not_override_later_pick_score() -> None:
    engine = PickEngine(ratings_data=_msh_pair_tiebreaker_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(31, 32),
        card_database=_msh_pair_tiebreaker_database(),
        pool_grp_ids=(20, 21),
        pick_index=6,
    )

    assert scored_pack.commitment.phase == "building"
    assert scored_pack.cards[0].card.grp_id == 32


def test_pair_win_rate_tiebreaker_does_not_override_later_color_signal() -> None:
    engine = PickEngine(ratings_data=_msh_pair_tiebreaker_data(red_gih=0.55))

    scored_pack = engine.score_pack(
        offered_grp_ids=(31, 32),
        card_database=_msh_pair_tiebreaker_database(),
        pool_grp_ids=(21, 22),
        pick_index=16,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert scored_pack.commitment.inferred_pair == "BR"
    assert by_id[31].color_fit == "off-color"
    assert by_id[32].color_fit == "on-color"
    assert scored_pack.cards[0].card.grp_id == 32


def test_pair_win_rate_tiebreaker_does_not_override_clear_card_signal() -> None:
    engine = PickEngine(ratings_data=_msh_pair_tiebreaker_data(red_gih=0.62))

    scored_pack = engine.score_pack(
        offered_grp_ids=(31, 32),
        card_database=_msh_pair_tiebreaker_database(),
        pool_grp_ids=(20, 21),
        pick_index=3,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert by_id[32].raw_score - by_id[31].raw_score > 3.0
    assert scored_pack.cards[0].card.grp_id == 32


def test_locked_pair_uses_pair_filtered_rating_when_samples_are_adequate() -> None:
    engine = PickEngine(ratings_data=_ratings_data_with_pair_filter())

    scored_pack = engine.score_pack(
        offered_grp_ids=(12,),
        card_database=_card_database(),
        pool_grp_ids=(1, 2),
        pick_index=16,
    )

    assert scored_pack.commitment.inferred_pair == "WU"
    assert scored_pack.cards[0].base_rating == 0.64


def test_supported_single_pip_bomb_is_marked_as_a_splash_and_can_win_pick() -> None:
    engine = PickEngine(ratings_data=_splash_ratings_data())
    database = _splash_card_database()

    scored_pack = engine.score_pack(
        offered_grp_ids=(105, 108),
        card_database=database,
        pool_grp_ids=(101, 102, 101, 102, 103, 104),
        pick_index=10,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert scored_pack.commitment.inferred_pair == "WU"
    assert by_id[105].color_fit == "splash-ready"
    assert by_id[105].splash.splash_color == "R"
    assert by_id[105].splash.available_sources == 3
    assert by_id[105].splash.required_sources == 3
    assert scored_pack.cards[0].card.grp_id == 105
    rendered = "\n".join(
        format_pack_offered_event(
            event=PackOfferedEvent(
                event_name="QuickDraft_TST_20260727",
                set_code="TST",
                pack_number=0,
                pick_number=9,
                offered_grp_ids=(105, 108),
                pool_grp_ids=(101, 102, 101, 102, 103, 104),
                account_id="account",
            ),
            card_database=database,
            scored_pack=scored_pack,
        )
    )
    assert "Splash R" in rendered


def test_freely_available_basic_does_not_block_splash_candidate() -> None:
    scored_pack = PickEngine(ratings_data=_splash_ratings_data()).score_pack(
        offered_grp_ids=(121, 105),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 103, 104),
        pick_index=10,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert by_id[121].score == 0
    assert by_id[105].color_fit == "splash-ready"
    assert by_id[105].splash.score_advantage is None


def test_disabling_splash_treats_same_bomb_as_an_ordinary_off_color_card() -> None:
    engine = PickEngine(
        ratings_data=_splash_ratings_data(),
        splash_enabled=False,
    )

    scored_pack = engine.score_pack(
        offered_grp_ids=(105, 108),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 103, 104),
        pick_index=10,
    )
    red_bomb = next(
        card for card in scored_pack.cards if card.card.grp_id == 105
    )

    assert scored_pack.splash_state.enabled is False
    assert red_bomb.color_fit == "off-color"
    assert red_bomb.splash.reasons == ("splashing is disabled",)


def test_active_splash_rejects_a_second_third_color_and_double_pips() -> None:
    engine = PickEngine(ratings_data=_splash_ratings_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(106, 107, 109),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 103, 104, 110, 105),
        pick_index=12,
    )
    by_id = {card.card.grp_id: card for card in scored_pack.cards}

    assert scored_pack.splash_state.active_color == "R"
    assert by_id[106].color_fit == "splash-ready"
    assert by_id[106].splash.required_sources == 4
    assert by_id[107].color_fit == "off-color"
    assert by_id[107].splash.reasons == ("the active splash color is R",)
    assert by_id[109].color_fit == "off-color"
    assert by_id[109].splash.reasons == ("card has too many off-color mana pips",)


def test_supported_existing_splash_color_beats_stronger_unsupported_color() -> None:
    scored_pack = PickEngine(ratings_data=_splash_ratings_data()).score_pack(
        offered_grp_ids=(108,),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 103, 104, 105, 107),
        pick_index=12,
    )

    assert scored_pack.splash_state.active_color == "R"


def test_fixing_land_is_marked_when_it_completes_active_splash_sources() -> None:
    scored_pack = PickEngine(ratings_data=_splash_ratings_data()).score_pack(
        offered_grp_ids=(103, 108),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 104, 105),
        pick_index=12,
    )
    fixing_land = next(card for card in scored_pack.cards if card.card.grp_id == 103)

    assert fixing_land.color_fit == "splash-fixer"
    assert fixing_land.splash.splash_color == "R"
    assert fixing_land.splash.fixing_sources == 2
    assert fixing_land.splash.planned_basic_sources == 1
    assert fixing_land.splash.available_sources == 3
    assert fixing_land.splash.required_sources == 3


def test_drafted_basic_is_not_counted_as_extra_splash_fixing() -> None:
    scored_pack = PickEngine(ratings_data=_splash_ratings_data()).score_pack(
        offered_grp_ids=(105, 108),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 103, 121),
        pick_index=10,
    )
    red_bomb = next(card for card in scored_pack.cards if card.card.grp_id == 105)

    assert scored_pack.splash_state.fixing_for(color="R") == 1
    assert red_bomb.color_fit == "splash-speculative"
    assert red_bomb.splash.available_sources == 2


def test_unsupported_a_grade_bomb_is_speculative_only_before_color_lock() -> None:
    engine = PickEngine(ratings_data=_splash_ratings_data())
    database = _splash_card_database()
    pool = (101, 102, 101, 102)

    building = engine.score_pack(
        offered_grp_ids=(105, 108),
        card_database=database,
        pool_grp_ids=pool,
        pick_index=10,
    )
    locked = engine.score_pack(
        offered_grp_ids=(105, 108),
        card_database=database,
        pool_grp_ids=pool,
        pick_index=16,
    )
    building_bomb = next(card for card in building.cards if card.card.grp_id == 105)
    locked_bomb = next(card for card in locked.cards if card.card.grp_id == 105)

    assert building_bomb.color_fit == "splash-speculative"
    assert building_bomb.splash.available_sources == 1
    assert building_bomb.splash.required_sources == 3
    assert locked_bomb.color_fit == "off-color"
    assert "speculative splashes are disabled after color lock" in (
        locked_bomb.splash.reasons
    )


def test_aggressive_pool_does_not_take_an_unsupported_speculative_splash() -> None:
    scored_pack = PickEngine(ratings_data=_splash_ratings_data()).score_pack(
        offered_grp_ids=(105, 108),
        card_database=_splash_card_database(),
        pool_grp_ids=(101, 102, 101, 102, 101, 102, 101, 102),
        pick_index=10,
    )
    red_bomb = next(card for card in scored_pack.cards if card.card.grp_id == 105)

    assert scored_pack.splash_state.aggressive is True
    assert red_bomb.color_fit == "off-color"
    assert "aggressive pools require supported exceptional splashes" in (
        red_bomb.splash.reasons
    )


def test_hybrid_symbol_payable_with_primary_color_does_not_require_splash() -> None:
    hybrid_card = _card(
        grp_id=120,
        name="Hybrid Primary Card",
        colors=("W", "R"),
        mana_cost="{2}{W/R}",
    )

    assert card_is_castable_in_pair(card=hybrid_card, base_pair="WU") is True
    assert splash_requirement(card=hybrid_card, base_pair="WU") == (None, 0)


def _ratings_data() -> SeventeenLandsData:
    return SeventeenLandsData(
        set_code="TST",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code="TST",
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            card_ratings={
                1: _stats(grp_id=1, name="Quick Bomb", gih=0.62, games_in_hand=900),
                2: _stats(
                    grp_id=2,
                    name="Thin Quick Card",
                    gih=None,
                    games_in_hand=120,
                    alsa=5.5,
                ),
                3: _stats(grp_id=3, name="Quick Filler", gih=0.50, games_in_hand=800),
                4: _stats(
                    grp_id=4,
                    name="Early Prior Card",
                    gih=None,
                    games_in_hand=0,
                    alsa=1.0,
                ),
                5: _stats(
                    grp_id=5,
                    name="Late Prior Card",
                    gih=None,
                    games_in_hand=0,
                    alsa=8.0,
                ),
                6: _stats(
                    grp_id=6,
                    name="Unknown Prior Card",
                    gih=None,
                    games_in_hand=0,
                    alsa=None,
                ),
                7: _stats(
                    grp_id=7,
                    name="White Test Card",
                    color="W",
                    gih=0.55,
                    games_in_hand=900,
                ),
                8: _stats(
                    grp_id=8,
                    name="Red Test Card",
                    color="R",
                    gih=0.55,
                    games_in_hand=900,
                ),
                9: _stats(
                    grp_id=9,
                    name="White Bomb",
                    color="W",
                    gih=0.65,
                    games_in_hand=900,
                ),
                10: _stats(
                    grp_id=10,
                    name="Blue Filler",
                    color="U",
                    gih=0.50,
                    games_in_hand=900,
                ),
                11: _stats(
                    grp_id=11,
                    name="Red Playable",
                    color="R",
                    gih=0.55,
                    games_in_hand=900,
                ),
                12: _stats(
                    grp_id=12,
                    name="Pair Filtered Card",
                    color="W",
                    gih=0.50,
                    games_in_hand=900,
                ),
            },
            pair_win_rates=_pair_win_rates(),
        ),
        fallback=SeventeenLandsFormatData(
            set_code="TST",
            event_format=PREMIER_DRAFT_FORMAT,
            fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            card_ratings={
                2: _stats(
                    grp_id=2,
                    name="Thin Quick Card",
                    gih=0.58,
                    games_in_hand=700,
                    alsa=4.0,
                ),
            },
            pair_win_rates=_pair_win_rates(),
        ),
        thin_sample_minimum=500,
    )


def _rated_basic_and_zero_data() -> SeventeenLandsData:
    data = _ratings_data()
    primary = replace(
        data.primary,
        card_ratings={
            **data.primary.card_ratings,
            120: _stats(
                grp_id=120,
                name="Draftable Zero",
                color="W",
                gih=-1.0,
                games_in_hand=900,
            ),
            121: _stats(
                grp_id=121,
                name="Mountain",
                color="C",
                gih=0.70,
                games_in_hand=900,
            ),
        },
    )
    return replace(data, primary=primary)


def _ratings_data_with_pair_filter() -> SeventeenLandsData:
    data = _ratings_data()
    pair_data = SeventeenLandsFormatData(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
        card_ratings={
            12: _stats(
                grp_id=12,
                name="Pair Filtered Card",
                color="W",
                gih=0.64,
                games_in_hand=900,
            ),
        },
        pair_win_rates=_pair_win_rates(),
    )
    return SeventeenLandsData(
        set_code=data.set_code,
        requested_format=data.requested_format,
        primary=data.primary,
        fallback=data.fallback,
        pair_card_ratings={"WU": pair_data},
        thin_sample_minimum=data.thin_sample_minimum,
    )


def _msh_pair_tiebreaker_data(*, red_gih: float = 0.552) -> SeventeenLandsData:
    return SeventeenLandsData(
        set_code="MSH",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code="MSH",
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            card_ratings={
                20: _stats(
                    grp_id=20,
                    name="White Start",
                    color="W",
                    gih=0.55,
                    games_in_hand=900,
                ),
                21: _stats(
                    grp_id=21,
                    name="Black Start",
                    color="B",
                    gih=0.55,
                    games_in_hand=900,
                ),
                22: _stats(
                    grp_id=22,
                    name="Red Start",
                    color="R",
                    gih=0.55,
                    games_in_hand=900,
                ),
                31: _stats(
                    grp_id=31,
                    name="Blue WU Lane Card",
                    color="U",
                    gih=0.55,
                    games_in_hand=900,
                ),
                32: _stats(
                    grp_id=32,
                    name="Red BR Lane Card",
                    color="R",
                    gih=red_gih,
                    games_in_hand=900,
                ),
                33: _stats(
                    grp_id=33,
                    name="Colorless Close Card",
                    color="C",
                    gih=0.552,
                    games_in_hand=900,
                ),
            },
            pair_win_rates=_msh_pair_win_rates(),
        ),
        fallback=None,
        thin_sample_minimum=500,
    )


def _splash_ratings_data() -> SeventeenLandsData:
    card_ratings = {
        101: _stats(
            grp_id=101,
            name="White Base Card",
            color="W",
            gih=0.55,
            games_in_hand=900,
        ),
        102: _stats(
            grp_id=102,
            name="Blue Base Card",
            color="U",
            gih=0.55,
            games_in_hand=900,
        ),
        105: _stats(
            grp_id=105,
            name="Red Splash Bomb",
            color="R",
            gih=0.70,
            games_in_hand=900,
        ),
        106: _stats(
            grp_id=106,
            name="Second Red Splash Bomb",
            color="R",
            gih=0.69,
            games_in_hand=900,
        ),
        107: _stats(
            grp_id=107,
            name="Green Splash Bomb",
            color="G",
            gih=0.72,
            games_in_hand=900,
        ),
        108: _stats(
            grp_id=108,
            name="White Solid Card",
            color="W",
            gih=0.58,
            games_in_hand=900,
        ),
        109: _stats(
            grp_id=109,
            name="Double Red Bomb",
            color="R",
            gih=0.71,
            games_in_hand=900,
        ),
        121: _stats(
            grp_id=121,
            name="Mountain",
            color="C",
            gih=0.70,
            games_in_hand=900,
        ),
    }
    card_ratings.update(
        {
            grp_id: _stats(
                grp_id=grp_id,
                name=f"Distribution Card {grp_id}",
                color="B",
                gih=0.50 + ((grp_id - 200) * 0.003),
                games_in_hand=900,
            )
            for grp_id in range(200, 220)
        }
    )
    return SeventeenLandsData(
        set_code="TST",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code="TST",
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            card_ratings=card_ratings,
            pair_win_rates=_pair_win_rates(),
        ),
        fallback=None,
        thin_sample_minimum=500,
    )


def _stats(
    *,
    grp_id: int,
    name: str,
    gih: float | None,
    games_in_hand: int,
    alsa: float | None = 4.5,
    color: str = "W",
) -> SeventeenCardStats:
    return SeventeenCardStats(
        grp_id=grp_id,
        name=name,
        color=color,
        rarity="common",
        average_last_seen_at=alsa,
        gih_win_rate=gih,
        opening_hand_win_rate=None,
        drawn_improvement_win_rate=None,
        sample_counts=RatingSampleCounts(
            seen=1000,
            picked=500,
            games_played=games_in_hand,
            opening_hand=200,
            games_in_hand=games_in_hand,
        ),
    )


def _pair_win_rates() -> dict[str, ColorPairWinRate]:
    return {
        "WU": ColorPairWinRate(pair="WU", wins=60, games=100, win_rate=0.6),
    }


def _msh_pair_win_rates() -> dict[str, ColorPairWinRate]:
    return {
        "WU": ColorPairWinRate(pair="WU", wins=606, games=1000, win_rate=0.606),
        "BR": ColorPairWinRate(pair="BR", wins=539, games=1000, win_rate=0.539),
    }


def _card_database() -> CardDatabase:
    return CardDatabase(
        cards={
            1: _card(grp_id=1, name="Quick Bomb", colors=("W",)),
            2: _card(grp_id=2, name="Thin Quick Card", colors=("U",)),
            3: _card(grp_id=3, name="Quick Filler", colors=("W",)),
            4: _card(grp_id=4, name="Early Prior Card", colors=("W",)),
            5: _card(grp_id=5, name="Late Prior Card", colors=("W",)),
            6: _card(grp_id=6, name="Unknown Prior Card", colors=("W",)),
            7: _card(grp_id=7, name="White Test Card", colors=("W",)),
            8: _card(grp_id=8, name="Red Test Card", colors=("R",)),
            9: _card(grp_id=9, name="White Bomb", colors=("W",)),
            10: _card(grp_id=10, name="Blue Filler", colors=("U",)),
            11: _card(grp_id=11, name="Red Playable", colors=("R",)),
            12: _card(grp_id=12, name="Pair Filtered Card", colors=("W",)),
            13: _card(
                grp_id=13,
                name="Arena Plains",
                colors=(),
                types=("Basic Land — Plains",),
                mana_cost=None,
            ),
            14: _card(
                grp_id=14,
                name="Wastes",
                colors=(),
                types=("Basic Land",),
                mana_cost=None,
            ),
            15: _card(
                grp_id=15,
                name="Snow-Covered Plains",
                colors=(),
                types=("Basic Snow Land — Plains",),
                mana_cost=None,
            ),
            16: _card(
                grp_id=16,
                name="Prairie Sanctuary",
                colors=(),
                types=("Land — Plains",),
                mana_cost=None,
            ),
        }
    )


def _msh_pair_tiebreaker_database() -> CardDatabase:
    return CardDatabase(
        cards={
            20: _card(grp_id=20, name="White Start", colors=("W",)),
            21: _card(grp_id=21, name="Black Start", colors=("B",)),
            22: _card(grp_id=22, name="Red Start", colors=("R",)),
            31: _card(grp_id=31, name="Blue WU Lane Card", colors=("U",)),
            32: _card(grp_id=32, name="Red BR Lane Card", colors=("R",)),
            33: _card(grp_id=33, name="Colorless Close Card", colors=()),
        }
    )


def _splash_card_database() -> CardDatabase:
    return CardDatabase(
        cards={
            101: _card(grp_id=101, name="White Base Card", colors=("W",)),
            102: _card(grp_id=102, name="Blue Base Card", colors=("U",)),
            103: _card(
                grp_id=103,
                name="Red Fixing Land One",
                colors=(),
                types=("Land",),
                mana_cost=None,
                produced_mana=("R",),
            ),
            104: _card(
                grp_id=104,
                name="Red Fixing Land Two",
                colors=(),
                types=("Land",),
                mana_cost=None,
                produced_mana=("R",),
            ),
            110: _card(
                grp_id=110,
                name="Red Fixing Land Three",
                colors=(),
                types=("Land",),
                mana_cost=None,
                produced_mana=("R",),
            ),
            120: _card(
                grp_id=120,
                name="Draftable Zero",
                colors=("W",),
            ),
            121: _card(
                grp_id=121,
                name="Mountain",
                colors=(),
                types=("Basic Land — Mountain",),
                mana_cost=None,
                mana_value=0.0,
                produced_mana=("R",),
            ),
            105: _card(
                grp_id=105,
                name="Red Splash Bomb",
                colors=("R",),
                mana_cost="{4}{R}",
            ),
            106: _card(
                grp_id=106,
                name="Second Red Splash Bomb",
                colors=("R",),
                mana_cost="{3}{R}",
            ),
            107: _card(
                grp_id=107,
                name="Green Splash Bomb",
                colors=("G",),
                mana_cost="{4}{G}",
            ),
            108: _card(
                grp_id=108,
                name="White Solid Card",
                colors=("W",),
                mana_cost="{2}{W}",
            ),
            109: _card(
                grp_id=109,
                name="Double Red Bomb",
                colors=("R",),
                mana_cost="{3}{R}{R}",
            ),
        }
    )


def _card(
    *,
    grp_id: int,
    name: str,
    colors: tuple[str, ...],
    types: tuple[str, ...] = ("Creature",),
    mana_cost: str | None = "{2}",
    mana_value: float | None = 2.0,
    produced_mana: tuple[str, ...] = (),
    set_code: str | None = None,
    arena_id: int | None = None,
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
        set_code=set_code,
        arena_id=arena_id,
    )


def test_pick_scoring_context_validates_its_pre_pick_contract() -> None:
    profile = _context_profile()
    ledger = _context_ledger(profile=profile, database=_card_database())
    context = PickScoringContext(set_profile=profile, role_ledger=ledger)
    assert ledger.profile_fingerprint == profile.fingerprint

    assert tuple(field.name for field in fields(PickScoringContext)) == (
        "set_profile",
        "role_ledger",
    )
    assert context.set_profile is profile
    assert context.role_ledger is ledger
    assert context.stage is ledger.stage
    assert not hasattr(context, "profile")
    assert not hasattr(context, "ledger")
    with pytest.raises(FrozenInstanceError):
        context.set_profile = profile
    with pytest.raises(TypeError):
        PickScoringContext(
            set_profile=profile,
            role_ledger=ledger,
            ledger=ledger,
        )
    with pytest.raises(TypeError, match="must be a SetProfile"):
        PickScoringContext(set_profile=object(), role_ledger=ledger)
    with pytest.raises(TypeError, match="must be a PoolRoleLedger"):
        PickScoringContext(set_profile=profile, role_ledger=object())
    with pytest.raises(ValueError, match="pre-pick projection"):
        PickScoringContext(
            set_profile=profile,
            role_ledger=replace(
                ledger,
                mode=COMPLETED_POOL,
                stage=None,
            ),
        )
    with pytest.raises(TypeError, match="stage must be a LedgerStage"):
        PickScoringContext(
            set_profile=profile,
            role_ledger=replace(ledger, stage=object()),
        )
    different_profile = replace(profile, confidence=0.75)
    with pytest.raises(ValueError, match="fingerprint"):
        PickScoringContext(
            set_profile=different_profile,
            role_ledger=ledger,
        )
    with pytest.raises(ValueError, match="does not match"):
        PickScoringContext(
            set_profile=profile,
            role_ledger=replace(ledger, profile_source="profile:early"),
        )


def test_scoring_context_preserves_generic_scores_but_exposes_context() -> None:
    database = _card_database()
    ratings_data = _ratings_data()
    profile = _context_profile()
    context = PickScoringContext(
        set_profile=profile,
        role_ledger=_context_ledger(profile=profile, database=database),
    )
    baseline = PickEngine(ratings_data=ratings_data).score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=database,
    )
    through_constructor = PickEngine(
        ratings_data=ratings_data,
        scoring_context=context,
    ).score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=database,
    )
    through_call = PickEngine(ratings_data=ratings_data).score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=database,
        scoring_context=context,
    )

    assert through_constructor.scoring_context is context
    assert through_call.scoring_context is context
    assert tuple(card.score for card in baseline.cards) == (90, 67, 67, 22)
    assert tuple(card.score for card in through_constructor.cards) == (
        90,
        67,
        67,
        22,
    )
    assert tuple(card.card.grp_id for card in through_constructor.cards) == tuple(
        card.card.grp_id for card in baseline.cards
    )
    assert tuple(card.score for card in through_call.cards) == (
        90,
        67,
        67,
        22,
    )
    assert tuple(card.score for card in through_call.cards) == tuple(
        card.score for card in baseline.cards
    )
    assert through_constructor.commitment.pick_index == context.stage.global_pick_index
    assert through_call.commitment.pick_index == context.stage.global_pick_index


def test_freely_available_basic_land_ignores_contextual_adjustments() -> None:
    generic_database = _card_database()
    basic_land = replace(
        generic_database.lookup(grp_id=13),
        set_code="TST",
        arena_id=13,
        produced_mana=("W", "U"),
    )
    database = CardDatabase(cards={**generic_database.cards, 13: basic_land})
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:13",
                assignments=(
                    RoleAssignment(
                        Role.FIXING,
                        parameters=ProducedResources(("W", "U")),
                    ),
                ),
            ),
        ),
        role_targets=(RoleTarget(Role.FIXING, 1),),
    )
    card = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(13,),
        pool_grp_ids=(),
    ).cards[0]

    assert card.freely_available_basic is True
    assert card.contextual_breakdown == ContextualScoreBreakdown()
    assert card.contextual_evidence == ()
    assert card.raw_score == 0
    assert card.score == 0


def test_contextual_terms_have_finite_individual_and_collective_bounds() -> None:
    positive = ContextualScoreBreakdown(
        role=MAX_ROLE_TERM,
        urgency=MAX_URGENCY_TERM,
        synergy=MAX_SYNERGY_TERM,
        fixing=MAX_FIXING_TERM,
    )
    negative = ContextualScoreBreakdown(
        redundancy=-MAX_REDUNDANCY_TERM,
        unsupported_payoff=-MAX_UNSUPPORTED_PAYOFF_TERM,
    )

    assert positive.aggregate == MAX_CONTEXTUAL_ADJUSTMENT
    assert negative.aggregate == -4.0
    serialized = positive.to_json()
    assert all(
        -MAX_CONTEXTUAL_ADJUSTMENT
        <= value
        <= MAX_CONTEXTUAL_ADJUSTMENT
        for value in (*serialized.values(), negative.aggregate)
    )
    assert serialized == {
        "role": MAX_ROLE_TERM,
        "urgency": MAX_URGENCY_TERM,
        "synergy": MAX_SYNERGY_TERM,
        "redundancy": 0.0,
        "unsupported_payoff": 0.0,
        "fixing": MAX_FIXING_TERM,
        "aggregate": MAX_CONTEXTUAL_ADJUSTMENT,
    }
    with pytest.raises(ValueError, match="finite"):
        ContextualScoreBreakdown(role=float("nan"))
    with pytest.raises(ValueError, match="between"):
        ContextualScoreBreakdown(urgency=MAX_URGENCY_TERM + 0.01)


def test_early_quality_dominates_a_small_contextual_role_bonus() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:7",
                assignments=(RoleAssignment(Role.DRAW),),
            ),
        ),
        role_targets=(RoleTarget(Role.DRAW, 1),),
    )
    scored_pack = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(7, 8),
        pool_grp_ids=(1,),
        global_pick_index=5,
        pack_number=0,
        pick_number=4,
        estimated_remaining_picks=37,
        ratings_data=_contextual_ratings(),
    )

    by_id = {card.card.grp_id: card for card in scored_pack.cards}
    assert scored_pack.cards[0].card.grp_id == 8
    assert 0 < by_id[7].contextual_breakdown.role < MAX_ROLE_TERM
    assert by_id[8].contextual_breakdown.aggregate == 0


def test_supported_semantic_package_adds_value_without_forcing_the_card() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:1",
                assignments=(RoleAssignment(Role.GO_WIDE_ENABLER),),
            ),
            ProfileCard(
                key="arena_id:2",
                assignments=(RoleAssignment(Role.GO_WIDE_PAYOFF),),
            ),
        )
    )
    scored_pack = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(2, 3),
        pool_grp_ids=(1,),
        ratings_data=_contextual_ratings(),
    )

    by_id = {card.card.grp_id: card for card in scored_pack.cards}
    assert by_id[2].contextual_breakdown.synergy > 0
    assert by_id[2].contextual_breakdown.unsupported_payoff == 0
    assert scored_pack.cards[0].card.grp_id == 3


def test_empirical_card_pair_synergy_does_not_change_contextual_score() -> None:
    database = _contextual_database()
    cards = (
        ProfileCard(
            key="arena_id:1",
            assignments=(RoleAssignment(Role.GO_WIDE_ENABLER),),
        ),
        ProfileCard(
            key="arena_id:2",
            assignments=(RoleAssignment(Role.GO_WIDE_PAYOFF),),
        ),
    )
    plain_profile = _contextual_profile(cards=cards)
    empirical_profile = _contextual_profile(
        cards=cards,
        synergy=(
            CardPairSynergy(
                first_card="arena_id:1",
                second_card="arena_id:2",
                value=99.0,
                samples=10000,
            ),
        ),
    )

    plain = _score_with_context(
        database=database,
        profile=plain_profile,
        offered_grp_ids=(2,),
        pool_grp_ids=(1,),
    ).cards[0]
    empirical = _score_with_context(
        database=database,
        profile=empirical_profile,
        offered_grp_ids=(2,),
        pool_grp_ids=(1,),
    ).cards[0]

    assert empirical.contextual_breakdown == plain.contextual_breakdown
    assert empirical.score == plain.score


def test_unsupported_payoff_is_penalized_without_an_enabler() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:1",
                assignments=(RoleAssignment(Role.GO_WIDE_PAYOFF),),
            ),
            ProfileCard(
                key="arena_id:2",
                assignments=(RoleAssignment(Role.GO_WIDE_PAYOFF),),
            ),
        )
    )
    card = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(2,),
        pool_grp_ids=(1,),
    ).cards[0]

    assert card.contextual_breakdown.unsupported_payoff < 0
    assert any("unsupported go_wide payoff" in item for item in card.contextual_evidence)

def test_dual_role_candidate_supplies_its_own_payoff_enabler() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:1",
                assignments=(RoleAssignment(Role.GO_WIDE_PAYOFF),),
            ),
            ProfileCard(
                key="arena_id:2",
                assignments=(
                    RoleAssignment(Role.GO_WIDE_ENABLER),
                    RoleAssignment(Role.GO_WIDE_PAYOFF),
                ),
            ),
        )
    )

    card = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(2,),
        pool_grp_ids=(1,),
    ).cards[0]

    assert card.contextual_breakdown.unsupported_payoff == 0
    assert not any(
        "unsupported go_wide payoff" in item for item in card.contextual_evidence
    )


def test_fixing_and_redundancy_terms_use_projected_pool_evidence() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:4",
                assignments=(RoleAssignment(Role.DRAW),),
            ),
            ProfileCard(
                key="arena_id:5",
                assignments=(RoleAssignment(Role.DRAW),),
            ),
            ProfileCard(
                key="arena_id:6",
                assignments=(
                    RoleAssignment(
                        Role.FIXING,
                        parameters=ProducedResources(("W", "U")),
                    ),
                ),
            ),
        ),
        role_targets=(
            RoleTarget(Role.DRAW, 1),
            RoleTarget(Role.FIXING, 2),
        ),
    )
    scored_pack = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(5, 6),
        pool_grp_ids=(4,),
    )

    by_id = {card.card.grp_id: card for card in scored_pack.cards}
    assert by_id[5].contextual_breakdown.redundancy < 0
    assert by_id[6].contextual_breakdown.fixing > 0
    assert any("redundancy pressure" in item for item in by_id[5].contextual_evidence)
    assert any("fixing need" in item for item in by_id[6].contextual_evidence)

def test_fixing_does_not_fallback_when_target_is_met_or_zero() -> None:
    database = _contextual_database()
    cards = (
        ProfileCard(
            key="arena_id:6",
            assignments=(
                RoleAssignment(
                    Role.FIXING,
                    parameters=ProducedResources(("W", "U")),
                ),
            ),
        ),
    )
    met_profile = _contextual_profile(
        cards=cards,
        role_targets=(RoleTarget(Role.FIXING, 1),),
    )
    zero_profile = _contextual_profile(
        cards=cards,
        role_targets=(RoleTarget(Role.FIXING, 0),),
    )

    met_card = _score_with_context(
        database=database,
        profile=met_profile,
        offered_grp_ids=(6,),
        pool_grp_ids=(6,),
    ).cards[0]
    zero_card = _score_with_context(
        database=database,
        profile=zero_profile,
        offered_grp_ids=(6,),
        pool_grp_ids=(),
    ).cards[0]

    assert met_card.contextual_breakdown.fixing == 0
    assert zero_card.contextual_breakdown.fixing == 0


def test_role_evidence_tie_uses_target_name_without_comparing_assignments() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:6",
                assignments=(
                    RoleAssignment(
                        Role.FIXING,
                        parameters=ProducedResources(("W",)),
                    ),
                    RoleAssignment(
                        Role.FIXING,
                        parameters=ProducedResources(("U",)),
                    ),
                ),
            ),
        ),
        role_targets=(RoleTarget(Role.FIXING, 1),),
    )

    card = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(6,),
        pool_grp_ids=(),
    ).cards[0]

    assert card.contextual_breakdown.role > 0
    assert any("fills fixing deficit" in item for item in card.contextual_evidence)


def test_generic_target_confidence_scales_redundancy_penalty() -> None:
    database = _contextual_database()
    cards = (
        ProfileCard(
            key="arena_id:5",
            assignments=(RoleAssignment(Role.DRAW),),
        ),
    )
    generic_card = _score_with_context(
        database=database,
        profile=_contextual_profile(cards=cards),
        offered_grp_ids=(5,),
        pool_grp_ids=(5,),
    ).cards[0]
    explicit_card = _score_with_context(
        database=database,
        profile=_contextual_profile(
            cards=cards,
            role_targets=(RoleTarget(Role.DRAW, 1),),
        ),
        offered_grp_ids=(5,),
        pool_grp_ids=(5,),
    ).cards[0]

    assert generic_card.contextual_breakdown.redundancy == pytest.approx(
        explicit_card.contextual_breakdown.redundancy * 0.25, abs=5e-7
    )


def test_low_confidence_target_scales_targeted_fixing() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:6",
                assignments=(
                    RoleAssignment(
                        Role.FIXING,
                        parameters=ProducedResources(("W", "U")),
                    ),
                ),
            ),
        ),
        role_targets=(RoleTarget(Role.FIXING, 2),),
    )
    high_confidence = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(6,),
        pool_grp_ids=(),
    ).cards[0]

    ledger = _context_ledger(profile=profile, database=database)
    low_confidence_coverage = tuple(
        replace(item, confidence=0.25)
        if item.name == Role.FIXING.value
        else item
        for item in ledger.target_coverage
    )
    low_confidence_ledger = replace(
        ledger,
        target_coverage=low_confidence_coverage,
    )
    low_confidence = PickEngine(
        scoring_context=PickScoringContext(
            set_profile=profile,
            role_ledger=low_confidence_ledger,
        )
    ).score_pack(
        offered_grp_ids=(6,),
        card_database=database,
        pool_grp_ids=(),
    ).cards[0]

    assert low_confidence.contextual_breakdown.fixing == pytest.approx(
        high_confidence.contextual_breakdown.fixing * 0.25
    )


def test_explanation_exposes_context_metadata_and_material_late_terms() -> None:
    database = _contextual_database()
    profile = _contextual_profile(
        cards=(
            ProfileCard(
                key="arena_id:7",
                assignments=(RoleAssignment(Role.DRAW),),
            ),
        ),
        role_targets=(RoleTarget(Role.DRAW, 1),),
        theme="patient card advantage",
    )
    card = _score_with_context(
        database=database,
        profile=profile,
        offered_grp_ids=(7,),
        pool_grp_ids=(1,),
    ).cards[0]
    explanation = recommendation_explanation(
        scored_card=card,
        inferred_pair="WU",
    )

    assert "context WU, theme patient card advantage" in explanation
    assert "mature profile (100% confidence)" in explanation
    assert "material terms:" in explanation
    assert "role +" in explanation
    assert "urgency +" in explanation
    assert "late missing-role urgency" in explanation


def _score_with_context(
    *,
    database: CardDatabase,
    profile: SetProfile,
    offered_grp_ids: tuple[int, ...],
    pool_grp_ids: tuple[int, ...],
    ratings_data: SeventeenLandsData | None = None,
    pack_number: int = 2,
    pick_number: int = 6,
    global_pick_index: int = 35,
    estimated_remaining_picks: int = 7,
) -> ScoredPack:
    ledger = project_pool_role_ledger(
        pool_before_pick=pool_grp_ids,
        pack_number=pack_number,
        pick_number=pick_number,
        global_pick_index=global_pick_index,
        estimated_remaining_picks=estimated_remaining_picks,
        card_database=database,
        ratings_data=ratings_data,
        set_profile=profile,
        likely_pair="WU",
    )
    context = PickScoringContext(set_profile=profile, role_ledger=ledger)
    return PickEngine(
        ratings_data=ratings_data,
        scoring_context=context,
    ).score_pack(
        offered_grp_ids=offered_grp_ids,
        card_database=database,
        pool_grp_ids=pool_grp_ids,
    )


def _contextual_profile(
    *,
    cards: tuple[ProfileCard, ...],
    role_targets: tuple[RoleTarget, ...] = (),
    theme: str | None = None,
    confidence: float = 1.0,
    synergy: tuple[CardPairSynergy, ...] = (),
) -> SetProfile:
    pair = PairProfile(
        pair="WU",
        role_targets=role_targets,
        synergy=synergy,
        theme=theme,
    )
    return SetProfile(
        set_code="TST",
        event_format="quickdraft",
        profile_version="contextual-test",
        generated_at="1970-01-01T00:00:00+00:00",
        source=SourceMetadata(provider="test"),
        maturity=ProfileMaturity.MATURE,
        samples=SampleSummary(total=1, by_pair=(("WU", 1),)),
        confidence=confidence,
        pairs=(pair,),
        role_profile=CompiledRoleProfile(set_code="TST", cards=cards),
    )


def _contextual_database() -> CardDatabase:
    return CardDatabase(
        cards={
            grp_id: _card(
                grp_id=grp_id,
                name=f"Context Card {grp_id}",
                colors=("W",),
                set_code="TST",
                arena_id=grp_id,
            )
            for grp_id in (1, 2, 3, 4, 7, 8)
        }
        | {
            5: _card(
                grp_id=5,
                name="Context Draw Redundant",
                colors=("W",),
                set_code="TST",
                arena_id=5,
            ),
            6: _card(
                grp_id=6,
                name="Context Fixing",
                colors=(),
                types=("Land",),
                mana_cost=None,
                mana_value=None,
                produced_mana=("W", "U"),
                set_code="TST",
                arena_id=6,
            ),
        },
    )


def _contextual_ratings() -> SeventeenLandsData:
    data = _ratings_data()
    primary = replace(
        data.primary,
        card_ratings={
            grp_id: _stats(
                grp_id=grp_id,
                name=f"Context Card {grp_id}",
                color="W",
                gih=0.9 if grp_id == 3 or grp_id == 8 else 0.5,
                games_in_hand=900,
            )
            for grp_id in (1, 2, 3, 4, 5, 6, 7, 8)
        },
    )
    return replace(data, primary=primary)


def _context_profile() -> SetProfile:
    return SetProfile(
        set_code="TST",
        event_format="quickdraft",
        profile_version="context-test",
        generated_at="1970-01-01T00:00:00+00:00",
        source=SourceMetadata(provider="test"),
        maturity=ProfileMaturity.MATURE,
        samples=SampleSummary(total=1, by_pair=(("WU", 1),)),
        confidence=1.0,
        pairs=(PairProfile(pair="WU"),),
    )


def _context_ledger(
    *,
    profile: SetProfile,
    database: CardDatabase,
) -> PoolRoleLedger:
    return project_pool_role_ledger(
        pool_before_pick=(),
        pack_number=2,
        pick_number=6,
        global_pick_index=35,
        estimated_remaining_picks=7,
        card_database=database,
        set_profile=profile,
        likely_pair="WU",
    )
