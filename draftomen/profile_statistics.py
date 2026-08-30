"""Deterministic shrinkage primitives for generated set profiles.

This module deliberately contains no file, network, or runtime-scoring concerns.
The generation model uses a 500-game prior strength for rates and a 100-deck
prior strength for generic targets.  Those values are public constants so a
profile records a model with explicit, reviewable hyperparameters rather than
silently inheriting a private scoring weight.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final


STATISTICS_VERSION: Final[int] = 1
"""Version of the offline profile statistical model."""

RATE_PRIOR_STRENGTH: Final[float] = 500.0
"""Default beta prior strength for card and pair rates, measured in games."""

TARGET_PRIOR_STRENGTH: Final[float] = 100.0
"""Default prior strength for generic target means, measured in decks."""


@dataclass(frozen=True, slots=True)
class BetaPrior:
    """A beta-distribution prior expressed by mean and equivalent samples.

    ``mean`` is constrained to a probability in ``[0, 1]``.  ``strength`` is
    the positive finite number of equivalent observations represented by the
    prior.  The frozen, slotted shape keeps the model safe to share between
    deterministic generation steps.
    """

    mean: float
    strength: float

    def __post_init__(self) -> None:
        mean = _finite_number(self.mean, name="mean")
        if not 0.0 <= mean <= 1.0:
            raise ValueError("Beta prior mean must be between 0 and 1.")

        strength = _finite_number(self.strength, name="strength")
        if strength <= 0.0:
            raise ValueError("Beta prior strength must be greater than zero.")

        object.__setattr__(self, "mean", mean)
        object.__setattr__(self, "strength", strength)


def beta_binomial_estimate(
    *,
    successes: int,
    trials: int,
    prior: BetaPrior,
) -> float:
    """Return a beta-binomial posterior mean for a bounded rate.

    Given a prior mean ``mu`` and strength ``k``, this is equivalent to beta
    parameters ``alpha = k * mu`` and ``beta = k * (1 - mu)``.  The posterior
    mean is therefore ``(successes + k * mu) / (trials + k)``.  Zero trials
    intentionally return the prior mean without fabricating raw evidence.
    """

    _non_negative_count(successes, name="successes")
    _non_negative_count(trials, name="trials")
    if successes > trials:
        raise ValueError("Beta-binomial successes cannot exceed trials.")
    if not isinstance(prior, BetaPrior):
        raise TypeError("beta-binomial prior must be a BetaPrior.")

    if trials == 0:
        return prior.mean

    estimate = (successes + (prior.strength * prior.mean)) / (
        trials + prior.strength
    )
    # Validated inputs make this mathematically bounded; retaining this guard
    # prevents an accidental future arithmetic change from leaking bad data.
    if not math.isfinite(float(estimate)) or not 0.0 <= estimate <= 1.0:
        raise ValueError("Beta-binomial estimate must be finite and bounded.")
    return float(estimate)


def shrink_mean(
    *,
    raw_value: float,
    samples: int,
    prior_value: float,
    prior_strength: float,
) -> float:
    """Shrink a finite raw mean toward a finite prior with weighted samples.

    ``samples`` is the number of observed observations and ``prior_strength``
    is the positive equivalent-observation weight of ``prior_value``.  The
    resulting value is ``(raw_value * samples + prior_value * prior_strength) /
    (samples + prior_strength)``.  With zero observations it is exactly the
    prior value; increasing observations moves it monotonically toward the raw
    value.
    """

    raw = _finite_number(raw_value, name="raw_value")
    sample_count = _non_negative_count(samples, name="samples")
    prior = _finite_number(prior_value, name="prior_value")
    strength = _finite_number(prior_strength, name="prior_strength")
    if strength <= 0.0:
        raise ValueError("Mean-shrinkage prior strength must be greater than zero.")

    if sample_count == 0:
        return prior

    estimate = (
        (raw * sample_count) + (prior * strength)
    ) / (sample_count + strength)
    if not math.isfinite(float(estimate)):
        raise ValueError("Shrunk mean must be finite.")
    return float(estimate)


def _finite_number(value: object, *, name: str) -> float:
    """Validate and normalize one finite real-valued input."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number.")
    try:
        normalized = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite.") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite.")
    return normalized


def _non_negative_count(value: object, *, name: str) -> int:
    """Validate one integral, non-negative observation count."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a non-negative integer.")
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


__all__ = [
    "BetaPrior",
    "RATE_PRIOR_STRENGTH",
    "STATISTICS_VERSION",
    "TARGET_PRIOR_STRENGTH",
    "beta_binomial_estimate",
    "shrink_mean",
]
