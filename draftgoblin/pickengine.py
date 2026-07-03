"""Card scoring and ranked pack recommendations.
Keep pick-quality math isolated from CLI and TUI rendering code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import COLOR_PAIRS, PICK_ENGINE, PickEngineConfig
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
class ColorCommitment:
    """Color inference for the current pick.
    The level is a 0.0-1.0 ramp from open to locked.
    """

    pick_index: int
    pool_size: int
    color_weights: tuple[tuple[str, float], ...]
    inferred_pair: str | None
    level: float

    @property
    def locked(self) -> bool:
        """Return whether commitment is fully locked.
        Locked picks may use pair-filtered ratings when available.
        """

        return self.level >= 1.0

    @property
    def phase(self) -> str:
        """Return a compact label for status-line rendering.
        This keeps CLI and TUI language consistent.
        """

        if self.level <= 0.0:
            return "open"

        if self.locked:
            return "locked"

        return "building"


@dataclass(frozen=True, slots=True)
class ScoredCard:
    """One offered card with its computed pick score.
    Rows keep source metadata so renderers can mark fallbacks clearly.
    """

    card: CardInfo
    rating: ResolvedCardRating
    original_index: int
    base_rating: float
    base_score: float
    color_factor: float
    adjusted_rating: float
    raw_score: float
    score: int
    source_label: str
    color_fit: str

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
    commitment: ColorCommitment


class PickEngine:
    """Score offered cards using 17Lands data and configured priors.
    Pool color weights progressively bias scores toward an inferred pair.
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
        pool_grp_ids: tuple[int, ...] = (),
        pick_index: int | None = None,
    ) -> ScoredPack:
        """Return offered cards sorted from highest score to lowest.
        Ties keep stronger raw ratings ahead, then preserve pack order.
        """

        commitment = _color_commitment(
            pool_grp_ids=pool_grp_ids,
            pick_index=_pick_index(pool_grp_ids=pool_grp_ids, pick_index=pick_index),
            card_database=card_database,
            ratings_data=self.ratings_data,
            config=self.config,
        )
        scored_cards = tuple(
            self._score_card(
                grp_id=grp_id,
                original_index=index,
                card_database=card_database,
                commitment=commitment,
            )
            for index, grp_id in enumerate(offered_grp_ids)
        )
        sorted_cards = tuple(sorted(scored_cards, key=_scored_card_sort_key))
        return ScoredPack(
            cards=sorted_cards,
            normalization=self.normalization,
            source_summary=_source_summary(cards=sorted_cards),
            commitment=commitment,
        )

    def _score_card(
        self,
        *,
        grp_id: int,
        original_index: int,
        card_database: CardDatabase,
        commitment: ColorCommitment,
    ) -> ScoredCard:
        card = card_database.lookup(grp_id=grp_id)
        rating = _rating_for(
            ratings_data=self.ratings_data,
            grp_id=grp_id,
            config=self.config,
            commitment=commitment,
        )
        base_rating = _base_rating(rating=rating, config=self.config)
        color_fit = _color_fit(card=card, commitment=commitment)
        color_factor = _color_factor(
            color_fit=color_fit,
            commitment=commitment,
            config=self.config,
        )
        base_score = _normalized_score(
            adjusted_rating=base_rating,
            normalization=self.normalization,
        )
        raw_score = _clamp(
            value=base_score * color_factor,
            lower=0.0,
            upper=100.0,
        )
        return ScoredCard(
            card=card,
            rating=rating,
            original_index=original_index,
            base_rating=base_rating,
            base_score=base_score,
            color_factor=color_factor,
            adjusted_rating=base_rating * color_factor,
            raw_score=raw_score,
            score=_integer_score(raw_score=raw_score),
            source_label=_source_label(rating=rating),
            color_fit=color_fit,
        )


def score_pack(
    *,
    offered_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData | None = None,
    config: PickEngineConfig = PICK_ENGINE,
    pool_grp_ids: tuple[int, ...] = (),
    pick_index: int | None = None,
) -> ScoredPack:
    """Convenience wrapper for callers that do not keep an engine instance.
    The reusable PickEngine class avoids rebuilding normalization per pack.
    """

    return PickEngine(ratings_data=ratings_data, config=config).score_pack(
        offered_grp_ids=offered_grp_ids,
        card_database=card_database,
        pool_grp_ids=pool_grp_ids,
        pick_index=pick_index,
    )


def _pick_index(
    *,
    pool_grp_ids: tuple[int, ...],
    pick_index: int | None,
) -> int:
    if pick_index is not None:
        return max(1, pick_index)

    return len(pool_grp_ids) + 1


def _color_commitment(
    *,
    pool_grp_ids: tuple[int, ...],
    pick_index: int,
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData | None,
    config: PickEngineConfig,
) -> ColorCommitment:
    weights = _pool_color_weights(
        pool_grp_ids=pool_grp_ids,
        card_database=card_database,
        ratings_data=ratings_data,
        config=config,
    )
    inferred_pair = _inferred_pair(weights=weights, config=config)
    level = _commitment_level(pick_index=pick_index, config=config)
    if inferred_pair is None:
        level = 0.0

    return ColorCommitment(
        pick_index=pick_index,
        pool_size=len(pool_grp_ids),
        color_weights=tuple((color, weights[color]) for color in weights),
        inferred_pair=inferred_pair,
        level=level,
    )


def _pool_color_weights(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData | None,
    config: PickEngineConfig,
) -> dict[str, float]:
    weights = _empty_color_weights()
    for grp_id in pool_grp_ids:
        card = card_database.lookup(grp_id=grp_id)
        if card.unknown or not card.colors:
            continue

        rating = _rating_for(
            ratings_data=ratings_data,
            grp_id=grp_id,
            config=config,
        )
        base_rating = _base_rating(rating=rating, config=config)
        weight = _pool_card_weight(base_rating=base_rating, config=config)
        for color in card.colors:
            if color in weights:
                weights[color] += weight

    return weights


def _empty_color_weights() -> dict[str, float]:
    colors: dict[str, float] = {}
    for pair in COLOR_PAIRS:
        for color in pair:
            colors.setdefault(color, 0.0)

    return colors


def _pool_card_weight(*, base_rating: float, config: PickEngineConfig) -> float:
    lower = min(config.pool_weight_minimum, config.pool_weight_maximum)
    upper = max(config.pool_weight_minimum, config.pool_weight_maximum)
    rating_delta = base_rating - config.neutral_prior_win_rate
    weight = config.pool_weight_baseline + (rating_delta * config.pool_weight_rating_scale)
    return _clamp(value=weight, lower=lower, upper=upper)


def _inferred_pair(
    *,
    weights: dict[str, float],
    config: PickEngineConfig,
) -> str | None:
    positive_colors = tuple(
        color for color, weight in weights.items() if weight > config.pool_weight_epsilon
    )
    if len(positive_colors) < config.minimum_pair_colors:
        return None

    return max(COLOR_PAIRS, key=lambda pair: _pair_weight(pair=pair, weights=weights))


def _pair_weight(*, pair: str, weights: dict[str, float]) -> float:
    return sum(weights.get(color, 0.0) for color in pair)


def _commitment_level(*, pick_index: int, config: PickEngineConfig) -> float:
    if pick_index <= config.open_pick_count:
        return 0.0

    ramp_start = max(config.commitment_start_pick, config.open_pick_count + 1)
    if pick_index < ramp_start:
        return 0.0

    if pick_index >= config.locked_pick_index:
        return 1.0

    ramp_span = config.locked_pick_index - ramp_start + 1
    if ramp_span <= 0:
        return 1.0

    return _clamp(
        value=(pick_index - ramp_start + 1) / ramp_span,
        lower=0.0,
        upper=1.0,
    )


def _color_fit(*, card: CardInfo, commitment: ColorCommitment) -> str:
    if card.unknown:
        return "unknown"

    if not card.colors:
        return "colorless"

    if commitment.level <= 0.0 or commitment.inferred_pair is None:
        return "open"

    if all(color in commitment.inferred_pair for color in card.colors):
        return "on-color"

    return "off-color"


def _color_factor(
    *,
    color_fit: str,
    commitment: ColorCommitment,
    config: PickEngineConfig,
) -> float:
    if commitment.level <= 0.0 or color_fit in {"open", "colorless", "unknown"}:
        return 1.0

    if color_fit == "on-color":
        factor = 1.0 + (commitment.level * (config.on_color_bonus_multiplier - 1.0))
        return max(0.0, factor)

    if color_fit == "off-color":
        penalty = 1.0 - config.off_color_penalty_multiplier
        factor = 1.0 - (commitment.level * penalty)
        return max(0.0, factor)

    return 1.0


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
    commitment: ColorCommitment | None = None,
) -> ResolvedCardRating:
    if ratings_data is None:
        return _neutral_rating(grp_id=grp_id, config=config)

    if commitment is not None and commitment.locked and commitment.inferred_pair is not None:
        return ratings_data.pair_rating_for(
            grp_id=grp_id,
            pair=commitment.inferred_pair,
        )

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
