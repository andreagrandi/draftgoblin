from __future__ import annotations

from datetime import UTC, datetime

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.events import PackOfferedEvent
from draftgoblin.pickengine import PickEngine
from draftgoblin.ranking import rank_scored_cards
from draftgoblin.replay import format_pack_offered_event
from draftgoblin.seventeen import (
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    ColorPairWinRate,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
)
from draftgoblin.splash import card_is_castable_in_pair, splash_requirement


def test_pick_engine_scores_and_sorts_with_fallback_sources() -> None:
    engine = PickEngine(ratings_data=_ratings_data())

    scored_pack = engine.score_pack(
        offered_grp_ids=(4, 3, 2, 1),
        card_database=_card_database(),
    )

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
            121: _card(
                grp_id=121,
                name="Mountain",
                colors=(),
                types=("Basic Land — Mountain",),
                mana_cost=None,
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
    produced_mana: tuple[str, ...] = (),
) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=colors,
        mana_value=2.0,
        rarity="common",
        types=types,
        mana_cost=mana_cost,
        produced_mana=produced_mana,
    )
