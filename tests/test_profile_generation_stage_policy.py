from __future__ import annotations

from dataclasses import replace

import pytest

from draftomen.profile_generation import ProfileGenerationStage
from draftomen.profile_generation_stage_policy import (
    DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS,
    ProfileGenerationEvidenceAvailability,
    ProfileGenerationStagePolicyError,
    ProfileGenerationStageThresholds,
    select_profile_generation_stage,
)
from draftomen.profile_input_acquisition import (
    ProfileInputAcquisitionOutcome,
    ProfileInputSourceReport,
)
from draftomen.profile_input_cache import ProfileInputCacheOutcome, ProfileInputSource


def _report(
    *,
    rating_rows: int | None = None,
    rating_samples: int | None = None,
    draft_rows: int | None = None,
    sha256: str | None = "a" * 64,
    content_bytes: int | None = 1,
) -> ProfileInputSourceReport:
    name = "17lands-ratings" if rating_rows is not None else "17lands-public-drafts"
    return ProfileInputSourceReport(
        source=ProfileInputSource(name=name, set_code="TST", event_format="quickdraft"),
        outcome=ProfileInputAcquisitionOutcome.ACQUIRED,
        cache_lookup_outcome=ProfileInputCacheOutcome.FRESH,
        sha256=sha256,
        content_bytes=content_bytes,
        rating_rows=rating_rows,
        rating_samples=rating_samples,
        draft_rows=draft_rows,
    )


def test_no_empirical_evidence_selects_metadata_explicitly() -> None:
    selection = select_profile_generation_stage(
        ratings_report=None,
        public_draft_report=None,
    )

    assert selection.stage is ProfileGenerationStage.METADATA
    assert selection.rationale == ("no-empirical-evidence",)
    assert selection.observed_availability == ProfileGenerationEvidenceAvailability(
        ratings_available=False,
        rating_rows=None,
        rating_samples=None,
        public_drafts_available=False,
        draft_rows=None,
    )
    assert selection.thresholds is DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS


@pytest.mark.parametrize(
    ("ratings", "drafts", "expected"),
    (
        (
            _report(rating_rows=2, rating_samples=3),
            None,
            ProfileGenerationStage.EARLY,
        ),
        (
            _report(rating_rows=2, rating_samples=3),
            _report(draft_rows=1),
            ProfileGenerationStage.MATURE,
        ),
        (
            _report(rating_rows=2, rating_samples=3),
            _report(draft_rows=0),
            ProfileGenerationStage.EARLY,
        ),
    ),
)
def test_thresholds_select_stages_deterministically(
    ratings: ProfileInputSourceReport,
    drafts: ProfileInputSourceReport | None,
    expected: ProfileGenerationStage,
) -> None:
    selection = select_profile_generation_stage(
        ratings_report=ratings,
        public_draft_report=drafts,
        thresholds=ProfileGenerationStageThresholds(
            early_rating_rows=2,
            early_rating_samples=3,
            mature_draft_rows=1,
        ),
    )

    assert selection.stage is expected


def test_missing_or_zero_evidence_does_not_upgrade() -> None:
    missing_ratings = select_profile_generation_stage(
        ratings_report=None,
        public_draft_report=_report(draft_rows=10),
    )
    zero_ratings = select_profile_generation_stage(
        ratings_report=_report(rating_rows=0, rating_samples=0),
        public_draft_report=None,
    )

    assert missing_ratings.stage is ProfileGenerationStage.METADATA
    assert zero_ratings.stage is ProfileGenerationStage.METADATA


@pytest.mark.parametrize(
    ("ratings", "drafts", "code"),
    (
        (
            _report(
                rating_rows=1,
                rating_samples=1,
                sha256=None,
                content_bytes=None,
            ),
            None,
            "ratings-pin-missing",
        ),
        (
            _report(rating_rows=1, rating_samples=1, content_bytes=None),
            None,
            "ratings-pin-partial",
        ),
        (
            replace(_report(), rating_rows=None, rating_samples=None),
            None,
            "ratings-counts-missing",
        ),
        (
            _report(rating_rows=1, rating_samples=1),
            _report(rating_rows=1, rating_samples=1),
            "drafts-counts-unexpected",
        ),
    ),
)
def test_ambiguous_evidence_raises_bounded_error(
    ratings: ProfileInputSourceReport,
    drafts: ProfileInputSourceReport | None,
    code: str,
) -> None:
    with pytest.raises(ProfileGenerationStagePolicyError) as raised:
        select_profile_generation_stage(
            ratings_report=ratings,
            public_draft_report=drafts,
        )

    assert raised.value.code == code
    assert len(str(raised.value)) <= 128
    assert "a" * 64 not in str(raised.value)


def test_selection_record_contains_thresholds_and_observed_counts_only() -> None:
    selection = select_profile_generation_stage(
        ratings_report=_report(rating_rows=7, rating_samples=11),
        public_draft_report=_report(draft_rows=13),
        thresholds=ProfileGenerationStageThresholds(
            early_rating_rows=5,
            early_rating_samples=10,
            mature_draft_rows=12,
        ),
    )
    record = selection.to_json()

    assert record == {
        "stage": "mature",
        "thresholds": {
            "early": {"rating_rows": 5, "rating_samples": 10},
            "mature": {
                "rating_rows": 5,
                "rating_samples": 10,
                "draft_rows": 12,
            },
        },
        "observed_availability": {
            "ratings_available": True,
            "rating_rows": 7,
            "rating_samples": 11,
            "public_drafts_available": True,
            "draft_rows": 13,
        },
        "rationale": ["mature-thresholds-met"],
    }
    serialized = selection.to_bytes().decode()
    assert "17lands" not in serialized
    assert "a" * 64 not in serialized
    assert "/" not in serialized
