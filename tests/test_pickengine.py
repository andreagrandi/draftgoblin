from __future__ import annotations

from datetime import UTC, datetime

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.pickengine import PickEngine
from draftgoblin.seventeen import (
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    ColorPairWinRate,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
)


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


def _card(*, grp_id: int, name: str, colors: tuple[str, ...]) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=colors,
        mana_value=2.0,
        rarity="common",
        types=("Creature",),
    )
