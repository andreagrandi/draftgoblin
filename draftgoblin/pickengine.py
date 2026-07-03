"""Card scoring and ranked pack recommendations.
Keep pick-quality math isolated from CLI and TUI rendering code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import PICK_ENGINE, PickEngineConfig
from draftgoblin.seventeen import (
    FORMAT_RATING_SOURCE,
    NEUTRAL_PRIOR_SOURCE,
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    RatingSampleCounts,
    RatingSourceMetadata,
    ResolvedCardRating,
    SeventeenLandsData,
)


@dataclass(frozen=True, slots=True)
class ScoreNormalization:
    """Rating bounds used to normalize raw card ratings.
    The neutral prior is centered at score 50 by construction.
    """

    lower_rating: float
    upper_rating: float
    neutral_rating: float


@dataclass(frozen=True, slots=True)
class ScoredCard:
    """One offered card with its computed pick score.
    Rows keep source metadata so renderers can mark fallbacks clearly.
    """

    card: CardInfo
    rating: ResolvedCardRating
    original_index: int
    base_rating: float
    color_factor: float
    adjusted_rating: float
    raw_score: float
    score: int
    source_label: str

    @property
    def no_data(self) -> bool:
        """Return whether this row used the neutral-prior fallback.
        Renderers mark these cards because no strong GIH sample was available.
        """

        return self.rating.neutral_prior

    @property
    def prior_adjusted_by_alsa(self) -> bool:
        """Return whether ALSA moved the neutral prior up or down.
        Missing ALSA leaves the neutral prior exactly centered.
        """

        return self.no_data and self.rating.average_last_seen_at is not None


@dataclass(frozen=True, slots=True)
class ScoredPack:
    """A score-sorted view of one offered pack.
    Source summary describes the actual data used in this pack.
    """

    cards: tuple[ScoredCard, ...]
    normalization: ScoreNormalization
    source_summary: str


class PickEngine:
    """Score offered cards using 17Lands data and configured priors.
    Color commitment is intentionally neutral until the next milestone.
    """

    def __init__(
        self,
        *,
        ratings_data: SeventeenLandsData | None = None,
        config: PickEngineConfig = PICK_ENGINE,
    ) -> None:
        self.ratings_data = ratings_data
        self.config = config
        self.normalization = _normalization_from_data(
            ratings_data=ratings_data,
            config=config,
        )

    def score_pack(
        self,
        *,
        offered_grp_ids: tuple[int, ...],
        card_database: CardDatabase,
    ) -> ScoredPack:
        """Return offered cards sorted from highest score to lowest.
        Ties keep stronger raw ratings ahead, then preserve pack order.
        """

        scored_cards = tuple(
            self._score_card(
                grp_id=grp_id,
                original_index=index,
                card_database=card_database,
            )
            for index, grp_id in enumerate(offered_grp_ids)
        )
        sorted_cards = tuple(sorted(scored_cards, key=_scored_card_sort_key))
        return ScoredPack(
            cards=sorted_cards,
            normalization=self.normalization,
            source_summary=_source_summary(cards=sorted_cards),
        )

    def _score_card(
        self,
        *,
        grp_id: int,
        original_index: int,
        card_database: CardDatabase,
    ) -> ScoredCard:
        card = card_database.lookup(grp_id=grp_id)
        rating = _rating_for(
            ratings_data=self.ratings_data,
            grp_id=grp_id,
            config=self.config,
        )
        base_rating = _base_rating(rating=rating, config=self.config)
        color_factor = 1.0
        adjusted_rating = base_rating * color_factor
        raw_score = _normalized_score(
            adjusted_rating=adjusted_rating,
            normalization=self.normalization,
        )
        return ScoredCard(
            card=card,
            rating=rating,
            original_index=original_index,
            base_rating=base_rating,
            color_factor=color_factor,
            adjusted_rating=adjusted_rating,
            raw_score=raw_score,
            score=_integer_score(raw_score=raw_score),
            source_label=_source_label(rating=rating),
        )


def score_pack(
    *,
    offered_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData | None = None,
    config: PickEngineConfig = PICK_ENGINE,
) -> ScoredPack:
    """Convenience wrapper for callers that do not keep an engine instance.
    The reusable PickEngine class avoids rebuilding normalization per pack.
    """

    return PickEngine(ratings_data=ratings_data, config=config).score_pack(
        offered_grp_ids=offered_grp_ids,
        card_database=card_database,
    )


def _normalization_from_data(
    *,
    ratings_data: SeventeenLandsData | None,
    config: PickEngineConfig,
) -> ScoreNormalization:
    distribution = _strong_rating_distribution(ratings_data=ratings_data)
    lower_percentile = _percentile(
        values=distribution,
        percentile=config.normalization_lower_percentile,
    )
    upper_percentile = _percentile(
        values=distribution,
        percentile=config.normalization_upper_percentile,
    )
    neutral = config.neutral_prior_win_rate
    half_span = config.normalization_min_half_span
    if lower_percentile is not None:
        half_span = max(half_span, neutral - lower_percentile)

    if upper_percentile is not None:
        half_span = max(half_span, upper_percentile - neutral)

    return ScoreNormalization(
        lower_rating=neutral - half_span,
        upper_rating=neutral + half_span,
        neutral_rating=neutral,
    )


def _strong_rating_distribution(
    *,
    ratings_data: SeventeenLandsData | None,
) -> tuple[float, ...]:
    if ratings_data is None:
        return ()

    return tuple(
        rating.gih_win_rate
        for rating in ratings_data.ratings.values()
        if rating.gih_win_rate is not None
    )


def _rating_for(
    *,
    ratings_data: SeventeenLandsData | None,
    grp_id: int,
    config: PickEngineConfig,
) -> ResolvedCardRating:
    if ratings_data is None:
        return _neutral_rating(grp_id=grp_id, config=config)

    return ratings_data.rating_for(grp_id=grp_id)


def _neutral_rating(*, grp_id: int, config: PickEngineConfig) -> ResolvedCardRating:
    return ResolvedCardRating(
        grp_id=grp_id,
        name=f"Unknown card {grp_id}",
        color=None,
        rarity=None,
        average_last_seen_at=None,
        gih_win_rate=None,
        opening_hand_win_rate=None,
        drawn_improvement_win_rate=None,
        sample_counts=RatingSampleCounts(
            seen=0,
            picked=0,
            games_played=0,
            opening_hand=0,
            games_in_hand=0,
        ),
        neutral_prior_score=config.neutral_prior_score,
        metadata=RatingSourceMetadata(
            requested_format=QUICK_DRAFT_FORMAT,
            source=NEUTRAL_PRIOR_SOURCE,
            source_format=None,
            fallback_reason="ratings-unavailable",
        ),
    )


def _base_rating(*, rating: ResolvedCardRating, config: PickEngineConfig) -> float:
    if rating.gih_win_rate is not None:
        return rating.gih_win_rate

    return config.neutral_prior_win_rate + _alsa_adjustment(
        average_last_seen_at=rating.average_last_seen_at,
        config=config,
    )


def _alsa_adjustment(
    *,
    average_last_seen_at: float | None,
    config: PickEngineConfig,
) -> float:
    if average_last_seen_at is None:
        return 0.0

    early = config.alsa_early_pick
    late = config.alsa_late_pick
    if late <= early:
        return 0.0

    clamped = _clamp(value=average_last_seen_at, lower=early, upper=late)
    midpoint = (early + late) / 2.0
    half_span = (late - early) / 2.0
    return ((midpoint - clamped) / half_span) * config.alsa_adjustment_max


def _normalized_score(
    *,
    adjusted_rating: float,
    normalization: ScoreNormalization,
) -> float:
    rating_span = normalization.upper_rating - normalization.lower_rating
    if rating_span <= 0:
        return 50.0

    score = ((adjusted_rating - normalization.lower_rating) / rating_span) * 100.0
    return _clamp(value=score, lower=0.0, upper=100.0)


def _integer_score(*, raw_score: float) -> int:
    return int(math.floor(_clamp(value=raw_score, lower=0.0, upper=100.0) + 0.5))


def _source_label(*, rating: ResolvedCardRating) -> str:
    if rating.metadata.source == NEUTRAL_PRIOR_SOURCE:
        return "Prior*"

    if rating.metadata.source != FORMAT_RATING_SOURCE:
        return "Unknown"

    if rating.metadata.source_format == QUICK_DRAFT_FORMAT:
        return "Quick"

    if rating.metadata.source_format == PREMIER_DRAFT_FORMAT:
        return "Premier"

    return rating.metadata.source_format or "Unknown"


def _source_summary(*, cards: tuple[ScoredCard, ...]) -> str:
    if not cards:
        return "none"

    uses_quick = any(card.source_label == "Quick" for card in cards)
    uses_premier = any(card.source_label == "Premier" for card in cards)
    uses_prior = any(card.no_data for card in cards)
    parts: list[str] = []
    if uses_quick:
        parts.append("QuickDraft")

    if uses_premier:
        parts.append("Premier fallback")

    if uses_prior:
        parts.append("neutral prior")

    return " + ".join(parts) if parts else "unknown"


def _scored_card_sort_key(card: ScoredCard) -> tuple[int, float, float, int]:
    return (-card.score, -card.raw_score, -card.base_rating, card.original_index)


def _percentile(*, values: tuple[float, ...], percentile: float) -> float | None:
    if not values:
        return None

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    clamped_percentile = _clamp(value=percentile, lower=0.0, upper=100.0)
    rank = (len(ordered) - 1) * (clamped_percentile / 100.0)
    lower_index = math.floor(rank)
    upper_index = math.ceil(rank)
    if lower_index == upper_index:
        return ordered[lower_index]

    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    fraction = rank - lower_index
    return lower_value + ((upper_value - lower_value) * fraction)


def _clamp(*, value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)
