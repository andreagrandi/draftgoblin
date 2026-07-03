"""Documented tunables for Draftgoblin.
Centralize defaults that later scoring and deck-building modules will share.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeckBuilderConfig:
    """Deck-builder structural defaults.
    Keep values aligned with Limited consensus until data-backed tuning exists.
    """

    target_spell_count: int = 23
    default_land_count: int = 17
    aggressive_land_count: int = 16
    top_heavy_land_count: int = 18
    creature_floor: int = 14
    creature_ceiling: int = 17
    minimum_two_drops: int = 5
    maximum_expensive_spells: int = 3
    main_color_source_floor: int = 7


@dataclass(frozen=True)
class PickEngineConfig:
    """Pick-engine scoring defaults.
    Scores stay integer-only for scan-friendly draft tables.
    """

    neutral_prior_score: float = 50.0
    neutral_prior_win_rate: float = 0.55
    thin_sample_minimum: int = 500
    alsa_adjustment_max: float = 0.03
    alsa_early_pick: float = 1.0
    alsa_late_pick: float = 8.0
    normalization_lower_percentile: float = 5.0
    normalization_upper_percentile: float = 95.0
    normalization_min_half_span: float = 0.05
    score_decimal_places: int = 0
    open_pick_count: int = 5
    commitment_start_pick: int = 6
    locked_pick_index: int = 16
    on_color_bonus_multiplier: float = 1.15
    off_color_penalty_multiplier: float = 0.75
    premier_fallback_enabled: bool = True


DECK_BUILDER = DeckBuilderConfig()
PICK_ENGINE = PickEngineConfig()

POLL_INTERVAL_SECONDS = 1.0
RATINGS_CACHE_TTL_HOURS = 24

COLOR_PAIRS = (
    "WU",
    "WB",
    "WR",
    "WG",
    "UB",
    "UR",
    "UG",
    "BR",
    "BG",
    "RG",
)
