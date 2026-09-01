"""Select an explicit profile-generation stage from staged evidence.

The policy consumes only privacy-safe source reports.  It never reads source
content or paths and does not invoke profile generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from draftomen.profile_generation import ProfileGenerationStage
from draftomen.profile_input_acquisition import ProfileInputSourceReport


_POLICY_ERROR_MESSAGES: dict[str, str] = {
    "invalid-thresholds": "profile generation stage policy thresholds are invalid.",
    "invalid-report": "profile generation stage policy evidence is invalid.",
    "ratings-pin-missing": "ratings evidence is ambiguous: its content pin is missing.",
    "ratings-pin-partial": "ratings evidence is ambiguous: its content pin is partial.",
    "ratings-counts-missing": "ratings evidence is ambiguous: rating availability is missing.",
    "ratings-counts-unexpected": "ratings evidence is ambiguous: it has draft availability.",
    "drafts-pin-missing": "public-draft evidence is ambiguous: its content pin is missing.",
    "drafts-pin-partial": "public-draft evidence is ambiguous: its content pin is partial.",
    "drafts-counts-missing": "public-draft evidence is ambiguous: draft availability is missing.",
    "drafts-counts-unexpected": "public-draft evidence is ambiguous: it has rating availability.",
}


class ProfileGenerationStagePolicyError(ValueError):
    """Raised when supplied evidence cannot be interpreted safely.

    Error messages are selected from a fixed bounded set.  They intentionally
    omit source identities, checksums, paths, raw rows, and exception text.
    """

    def __init__(self, code: str) -> None:
        normalized = (
            code
            if isinstance(code, str) and code in _POLICY_ERROR_MESSAGES
            else "invalid-report"
        )
        self.code = normalized
        super().__init__(_POLICY_ERROR_MESSAGES[normalized])


@dataclass(frozen=True, slots=True)
class ProfileGenerationStageThresholds:
    """Positive empirical availability thresholds for each generation stage."""

    early_rating_rows: int = 1
    early_rating_samples: int = 1
    mature_draft_rows: int = 1

    def __post_init__(self) -> None:
        for value in (
            self.early_rating_rows,
            self.early_rating_samples,
            self.mature_draft_rows,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ProfileGenerationStagePolicyError("invalid-thresholds")

    def to_json(self) -> dict[str, Any]:
        """Return the complete threshold predicates used by the selector."""

        early = {
            "rating_rows": self.early_rating_rows,
            "rating_samples": self.early_rating_samples,
        }
        return {
            "early": early,
            "mature": {
                "rating_rows": self.early_rating_rows,
                "rating_samples": self.early_rating_samples,
                "draft_rows": self.mature_draft_rows,
            },
        }


DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS = ProfileGenerationStageThresholds()


@dataclass(frozen=True, slots=True)
class ProfileGenerationEvidenceAvailability:
    """Observed, path-free empirical availability retained in a selection."""

    ratings_available: bool
    rating_rows: int | None
    rating_samples: int | None
    public_drafts_available: bool
    draft_rows: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.ratings_available, bool) or not isinstance(
            self.public_drafts_available, bool
        ):
            raise ProfileGenerationStagePolicyError("invalid-report")
        _validate_optional_count_pair(self.rating_rows, self.rating_samples)
        _validate_optional_count(self.draft_rows)
        if self.ratings_available != (self.rating_rows is not None):
            raise ProfileGenerationStagePolicyError("invalid-report")
        if self.public_drafts_available != (self.draft_rows is not None):
            raise ProfileGenerationStagePolicyError("invalid-report")

    def to_json(self) -> dict[str, Any]:
        """Return only bounded observed availability and count values."""

        return {
            "draft_rows": self.draft_rows,
            "public_drafts_available": self.public_drafts_available,
            "rating_rows": self.rating_rows,
            "rating_samples": self.rating_samples,
            "ratings_available": self.ratings_available,
        }


@dataclass(frozen=True, slots=True)
class ProfileGenerationStageSelection:
    """The explicit stage and privacy-safe evidence used to select it."""

    stage: ProfileGenerationStage
    thresholds: ProfileGenerationStageThresholds
    observed_availability: ProfileGenerationEvidenceAvailability
    rationale: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.stage, ProfileGenerationStage):
            raise ProfileGenerationStagePolicyError("invalid-report")
        if not isinstance(self.thresholds, ProfileGenerationStageThresholds):
            raise ProfileGenerationStagePolicyError("invalid-thresholds")
        if not isinstance(self.observed_availability, ProfileGenerationEvidenceAvailability):
            raise ProfileGenerationStagePolicyError("invalid-report")
        try:
            rationale = tuple(self.rationale)
        except (TypeError, ValueError) as error:
            raise ProfileGenerationStagePolicyError("invalid-report") from error
        if not rationale or any(
            not isinstance(token, str) or token not in _RATIONALE_TOKENS
            for token in rationale
        ):
            raise ProfileGenerationStagePolicyError("invalid-report")
        if len(rationale) > 1:
            raise ProfileGenerationStagePolicyError("invalid-report")
        object.__setattr__(self, "rationale", rationale)

    def to_json(self) -> dict[str, Any]:
        """Return a deterministic, path-free selection record."""

        return {
            "observed_availability": self.observed_availability.to_json(),
            "rationale": list(self.rationale),
            "stage": self.stage.value,
            "thresholds": self.thresholds.to_json(),
        }

    def to_bytes(self) -> bytes:
        """Serialize this selection using canonical compact JSON."""

        return (
            json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")


_RATIONALE_TOKENS = frozenset(
    {
        "no-empirical-evidence",
        "early-thresholds-not-met",
        "early-thresholds-met",
        "mature-thresholds-met",
    }
)


def select_profile_generation_stage(
    *,
    ratings_report: ProfileInputSourceReport | None,
    public_draft_report: ProfileInputSourceReport | None,
    thresholds: ProfileGenerationStageThresholds = DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS,
) -> ProfileGenerationStageSelection:
    """Select mature, early, or metadata from explicitly pinned role reports.

    ``None`` represents normally unavailable optional evidence.  Any supplied
    report must be complete and role-shaped; ambiguous evidence raises rather
    than silently falling back to metadata.
    """

    if not isinstance(thresholds, ProfileGenerationStageThresholds):
        raise ProfileGenerationStagePolicyError("invalid-thresholds")
    _validate_supplied_report(ratings_report, role="ratings")
    _validate_supplied_report(public_draft_report, role="public-drafts")

    observed = ProfileGenerationEvidenceAvailability(
        ratings_available=ratings_report is not None,
        rating_rows=None if ratings_report is None else ratings_report.rating_rows,
        rating_samples=None if ratings_report is None else ratings_report.rating_samples,
        public_drafts_available=public_draft_report is not None,
        draft_rows=None if public_draft_report is None else public_draft_report.draft_rows,
    )

    early_met = (
        ratings_report is not None
        and ratings_report.rating_rows >= thresholds.early_rating_rows
        and ratings_report.rating_samples >= thresholds.early_rating_samples
    )
    mature_met = (
        early_met
        and public_draft_report is not None
        and public_draft_report.draft_rows >= thresholds.mature_draft_rows
    )
    if mature_met:
        stage = ProfileGenerationStage.MATURE
        rationale = ("mature-thresholds-met",)
    elif early_met:
        stage = ProfileGenerationStage.EARLY
        rationale = ("early-thresholds-met",)
    elif ratings_report is None and public_draft_report is None:
        stage = ProfileGenerationStage.METADATA
        rationale = ("no-empirical-evidence",)
    else:
        stage = ProfileGenerationStage.METADATA
        rationale = ("early-thresholds-not-met",)

    return ProfileGenerationStageSelection(
        stage=stage,
        thresholds=thresholds,
        observed_availability=observed,
        rationale=rationale,
    )


def _validate_supplied_report(
    report: ProfileInputSourceReport | None,
    *,
    role: str,
) -> None:
    if report is None:
        return
    if not isinstance(report, ProfileInputSourceReport):
        raise ProfileGenerationStagePolicyError("invalid-report")

    has_digest = report.sha256 is not None
    has_size = report.content_bytes is not None
    if not has_digest and not has_size:
        raise ProfileGenerationStagePolicyError(f"{role}-pin-missing")
    if has_digest != has_size:
        raise ProfileGenerationStagePolicyError(f"{role}-pin-partial")

    if role == "ratings":
        if report.draft_rows is not None:
            raise ProfileGenerationStagePolicyError("ratings-counts-unexpected")
        if report.rating_rows is None or report.rating_samples is None:
            raise ProfileGenerationStagePolicyError("ratings-counts-missing")
    else:
        if report.rating_rows is not None or report.rating_samples is not None:
            raise ProfileGenerationStagePolicyError("drafts-counts-unexpected")
        if report.draft_rows is None:
            raise ProfileGenerationStagePolicyError("drafts-counts-missing")


def _validate_optional_count_pair(first: int | None, second: int | None) -> None:
    if (first is None) != (second is None):
        raise ProfileGenerationStagePolicyError("invalid-report")
    if first is not None:
        _validate_optional_count(first)
        _validate_optional_count(second)


def _validate_optional_count(value: int | None) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ProfileGenerationStagePolicyError("invalid-report")


__all__ = [
    "DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS",
    "ProfileGenerationEvidenceAvailability",
    "ProfileGenerationStagePolicyError",
    "ProfileGenerationStageSelection",
    "ProfileGenerationStageThresholds",
    "select_profile_generation_stage",
]
