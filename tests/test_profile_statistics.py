from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from draftomen.profile_statistics import (
    RATE_PRIOR_STRENGTH,
    STATISTICS_VERSION,
    TARGET_PRIOR_STRENGTH,
    BetaPrior,
    beta_binomial_estimate,
    shrink_mean,
)


def test_statistics_model_version_and_default_strengths_are_explicit() -> None:
    assert STATISTICS_VERSION == 1
    assert RATE_PRIOR_STRENGTH == 500.0
    assert TARGET_PRIOR_STRENGTH == 100.0


def test_beta_prior_is_immutable_and_normalizes_numeric_values() -> None:
    prior = BetaPrior(mean=1, strength=500)

    assert prior.mean == 1.0
    assert prior.strength == 500.0
    with pytest.raises(FrozenInstanceError):
        prior.mean = 0.5  # type: ignore[misc]


@pytest.mark.parametrize("mean", (-0.01, 1.01, math.inf, -math.inf, math.nan))
def test_beta_prior_rejects_invalid_means(mean: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        BetaPrior(mean=mean, strength=500.0)


@pytest.mark.parametrize("strength", (0.0, -1.0, math.inf, -math.inf, math.nan))
def test_beta_prior_rejects_invalid_strengths(strength: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        BetaPrior(mean=0.5, strength=strength)


@pytest.mark.parametrize(
    ("mean", "strength"),
    ((True, 500.0), (0.5, False), ("0.5", 500.0), (0.5, "500")),
)
def test_beta_prior_rejects_non_numeric_values(mean: object, strength: object) -> None:
    with pytest.raises(TypeError):
        BetaPrior(mean=mean, strength=strength)  # type: ignore[arg-type]


def test_beta_binomial_estimate_matches_documented_formula() -> None:
    prior = BetaPrior(mean=0.25, strength=500.0)

    estimate = beta_binomial_estimate(successes=125, trials=250, prior=prior)

    assert estimate == pytest.approx((125 + (500.0 * 0.25)) / (250 + 500.0))
    assert estimate == pytest.approx(1.0 / 3.0)
    assert 0.0 <= estimate <= 1.0


def test_beta_binomial_zero_trials_returns_prior_mean() -> None:
    prior = BetaPrior(mean=0.73, strength=500.0)

    assert beta_binomial_estimate(successes=0, trials=0, prior=prior) == 0.73


def test_beta_binomial_high_sample_rate_approaches_raw_rate() -> None:
    prior = BetaPrior(mean=0.2, strength=RATE_PRIOR_STRENGTH)

    estimate = beta_binomial_estimate(
        successes=9_999,
        trials=10_000,
        prior=prior,
    )

    assert estimate < 0.9999
    assert estimate > 0.9
    assert estimate == pytest.approx(
        (9_999 + (RATE_PRIOR_STRENGTH * 0.2))
        / (10_000 + RATE_PRIOR_STRENGTH)
    )


@pytest.mark.parametrize(
    ("successes", "trials"),
    ((-1, 10), (11, 10), (1.0, 10), (1, 10.0), (False, 10)),
)
def test_beta_binomial_rejects_invalid_counts(
    successes: object,
    trials: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        beta_binomial_estimate(
            successes=successes,  # type: ignore[arg-type]
            trials=trials,  # type: ignore[arg-type]
            prior=BetaPrior(mean=0.5, strength=500.0),
        )


def test_beta_binomial_requires_beta_prior() -> None:
    with pytest.raises(TypeError):
        beta_binomial_estimate(successes=1, trials=2, prior=0.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw_value", (math.inf, -math.inf, math.nan))
def test_shrink_mean_rejects_non_finite_raw_values(raw_value: float) -> None:
    with pytest.raises(ValueError):
        shrink_mean(
            raw_value=raw_value,
            samples=10,
            prior_value=0.5,
            prior_strength=100.0,
        )


def test_shrink_mean_matches_weighted_formula_and_zero_sample_behavior() -> None:
    assert shrink_mean(
        raw_value=0.9,
        samples=100,
        prior_value=0.5,
        prior_strength=100.0,
    ) == pytest.approx(0.7)
    assert shrink_mean(
        raw_value=0.9,
        samples=0,
        prior_value=0.5,
        prior_strength=100.0,
    ) == 0.5


def test_shrink_mean_moves_monotonically_toward_raw_value_with_more_samples() -> None:
    values = tuple(
        shrink_mean(
            raw_value=0.8,
            samples=samples,
            prior_value=0.2,
            prior_strength=TARGET_PRIOR_STRENGTH,
        )
        for samples in (0, 1, 10, 100, 1_000)
    )

    assert values == tuple(sorted(values))
    assert values[0] == 0.2
    assert values[-1] < 0.8
    assert values[-1] > values[-2]


@pytest.mark.parametrize(
    ("raw_value", "samples", "prior_value", "prior_strength"),
    (
        (0.5, -1, 0.5, 100.0),
        (0.5, 1.0, 0.5, 100.0),
        (0.5, False, 0.5, 100.0),
        (0.5, 1, math.inf, 100.0),
        (0.5, 1, 0.5, 0.0),
        (0.5, 1, 0.5, -1.0),
        (0.5, 1, 0.5, math.inf),
        (0.5, 1, 0.5, math.nan),
    ),
)
def test_shrink_mean_rejects_invalid_inputs(
    raw_value: object,
    samples: object,
    prior_value: object,
    prior_strength: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        shrink_mean(
            raw_value=raw_value,  # type: ignore[arg-type]
            samples=samples,  # type: ignore[arg-type]
            prior_value=prior_value,  # type: ignore[arg-type]
            prior_strength=prior_strength,  # type: ignore[arg-type]
        )
