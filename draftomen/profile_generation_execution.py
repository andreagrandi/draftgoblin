"""UI-neutral execution of one staged profile-generation environment.

This module consumes exactly one strict staged input bundle, chooses the
explicit generation stage, produces bounded classification diagnostics, and
runs the existing generator through the public publication validation gate.
No artifact or publication filesystem is touched here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, TypeAlias

from draftomen.profile_generation import (
    DEFAULT_PROFILE_GENERATION_CONFIG,
    ProfileGenerationConfig,
    ProfileGenerationResult,
    generate_set_profile,
)
from draftomen.set_profile import ProfileMaturity
from draftomen.profile_generation_stage_policy import (
    DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS,
    ProfileGenerationStageSelection,
    ProfileGenerationStageThresholds,
    select_profile_generation_stage,
)
from draftomen.profile_publication import (
    ValidatedProfileGeneration,
    validate_profile_generation,
)
from draftomen.profile_refresh_execution import (
    PathInput,
    load_staged_profile_build_bundle,
)
from draftomen.refresh_plan import PlannedEnvironment
from draftomen.semantic_roles import RoleClassifier


PROFILE_GENERATION_EXECUTION_SCHEMA_VERSION = 1
_MAX_DIAGNOSTICS = 64
_MAX_DIAGNOSTIC_FIELD_LENGTH = 128

# These strings are intentionally fixed.  In particular, classifier exception
# text is never carried into a result or its canonical serialization.
CLASSIFICATION_GAP_REASON = "classification produced no role assignments"
CLASSIFICATION_ERROR_REASON = "classification failed while inspecting the card"


class ProfileGenerationExecutionError(RuntimeError):
    """Raised when the execution result contract itself is malformed."""


class ProfileGenerationEnvironmentOutcome(str, Enum):
    """The finite outcomes of one environment execution."""

    PUBLICATION_ELIGIBLE = "publication-eligible"
    FAILED = "failed"


class ProfileGenerationFailurePhase(str, Enum):
    """The finite execution phase at which a bounded failure occurred."""

    REFRESH_EXECUTION = "refresh-execution"
    STAGED_BUNDLE_LOAD = "staged-bundle-load"
    STAGE_SELECTION = "stage-selection"
    GENERATION = "generation"
    VALIDATION = "validation"


class ProfileGenerationFailureReason(str, Enum):
    """Finite, path-free failure reasons exposed by this service."""

    REFRESH_EXECUTION_FAILED = "refresh-execution-failed"
    STAGED_BUNDLE_LOAD_FAILED = "staged-bundle-load-failed"
    STAGE_SELECTION_FAILED = "stage-selection-failed"
    GENERATION_FAILED = "generation-failed"
    VALIDATION_FAILED = "validation-failed"


@dataclass(frozen=True, slots=True)
class ProfileGenerationDiagnostic:
    """One bounded, path-free classification diagnostic."""

    card_key: str
    card_name: str | None
    mechanic: str | None
    reason: str

    def __post_init__(self) -> None:
        card_key = _bounded_required(self.card_key, "card_key")
        reason = _bounded_required(self.reason, "reason")
        card_name = _bounded_optional(self.card_name)
        mechanic = _bounded_optional(self.mechanic)
        object.__setattr__(self, "card_key", card_key)
        object.__setattr__(self, "card_name", card_name)
        object.__setattr__(self, "mechanic", mechanic)
        object.__setattr__(self, "reason", reason)

    def to_json(self) -> dict[str, str | None]:
        return {
            "card_key": self.card_key,
            "card_name": self.card_name,
            "mechanic": self.mechanic,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProfileGenerationEnvironmentResult:
    """One immutable, bounded result for one planned environment."""

    environment: PlannedEnvironment
    outcome: ProfileGenerationEnvironmentOutcome | str
    selection: ProfileGenerationStageSelection | None = None
    diagnostics: tuple[ProfileGenerationDiagnostic, ...] = ()
    diagnostic_total: int = 0
    diagnostics_omitted: int = 0
    skip_count: int | None = None
    error_count: int | None = None
    generation: ProfileGenerationResult | None = None
    validated: ValidatedProfileGeneration | None = None
    failure_phase: ProfileGenerationFailurePhase | str | None = None
    failure_reason: ProfileGenerationFailureReason | str | None = None
    input_sources: tuple[Mapping[str, str], ...] = ()
    schema_version: int = PROFILE_GENERATION_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.environment, PlannedEnvironment):
            raise ProfileGenerationExecutionError("environment must be a PlannedEnvironment")
        if self.schema_version != PROFILE_GENERATION_EXECUTION_SCHEMA_VERSION:
            raise ProfileGenerationExecutionError("unsupported profile generation execution schema")
        try:
            outcome = (
                self.outcome
                if isinstance(self.outcome, ProfileGenerationEnvironmentOutcome)
                else ProfileGenerationEnvironmentOutcome(self.outcome)
            )
        except (TypeError, ValueError) as error:
            raise ProfileGenerationExecutionError("profile generation outcome is invalid") from error
        object.__setattr__(self, "outcome", outcome)

        selection = self.selection
        if selection is not None and not isinstance(selection, ProfileGenerationStageSelection):
            raise ProfileGenerationExecutionError("profile generation stage selection is invalid")

        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, ProfileGenerationDiagnostic) for item in diagnostics):
            raise ProfileGenerationExecutionError("profile generation diagnostics are invalid")
        diagnostics = tuple(sorted(set(diagnostics), key=_diagnostic_sort_key))
        if len(diagnostics) > _MAX_DIAGNOSTICS:
            raise ProfileGenerationExecutionError("profile generation diagnostics exceed the bound")
        object.__setattr__(self, "diagnostics", diagnostics)

        input_sources = tuple(self.input_sources)
        for item in input_sources:
            if not isinstance(item, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in item.items()
            ):
                raise ProfileGenerationExecutionError("profile generation input sources are invalid")
        object.__setattr__(self, "input_sources", input_sources)

        for name in ("diagnostic_total", "diagnostics_omitted"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProfileGenerationExecutionError(f"{name} is invalid")
        if self.diagnostic_total < len(diagnostics):
            raise ProfileGenerationExecutionError("diagnostic total is below retained diagnostics")
        if self.diagnostics_omitted != self.diagnostic_total - len(diagnostics):
            raise ProfileGenerationExecutionError("diagnostic omission count does not reconcile")

        if self.outcome is ProfileGenerationEnvironmentOutcome.PUBLICATION_ELIGIBLE:
            if selection is None or not isinstance(self.generation, ProfileGenerationResult):
                raise ProfileGenerationExecutionError("eligible result requires stage selection and generation")
            if not isinstance(self.validated, ValidatedProfileGeneration):
                raise ProfileGenerationExecutionError("eligible result requires validated payload")
            if self.failure_phase is not None or self.failure_reason is not None:
                raise ProfileGenerationExecutionError("eligible result cannot carry a failure")
            _validate_count(self.skip_count, "skip_count", required=True)
            _validate_count(self.error_count, "error_count", required=True)

            try:
                expected_validated = validate_profile_generation(
                    generation=self.generation,
                    set_code=self.environment.set_code.casefold(),
                    event_format=self.environment.event_format.casefold(),
                    stage=selection.stage.value,
                )
                if not isinstance(expected_validated, ValidatedProfileGeneration):
                    raise ProfileGenerationExecutionError(
                        "eligible result validator returned an invalid payload"
                    )
            except Exception as error:  # noqa: BLE001 - malformed success payloads are rejected
                raise ProfileGenerationExecutionError(
                    "eligible result generation failed publication validation"
                ) from error
            if (
                self.validated.profile_bytes != expected_validated.profile_bytes
                or self.validated.gzip_bytes != expected_validated.gzip_bytes
                or self.validated.report_bytes != expected_validated.report_bytes
            ):
                raise ProfileGenerationExecutionError(
                    "eligible result validated payload does not match generation"
                )

            report = self.generation.report
            expected_maturity = {
                "metadata": ProfileMaturity.METADATA_ONLY,
                "early": ProfileMaturity.EARLY,
                "mature": ProfileMaturity.MATURE,
            }.get(selection.stage.value)
            if (
                expected_maturity is None
                or self.generation.profile.maturity is not expected_maturity
                or report.generated_at != self.generation.profile.generated_at
            ):
                raise ProfileGenerationExecutionError(
                    "eligible result generation identity and stage do not reconcile"
                )
            try:
                expected_skip_count = sum(report.skip_reasons.values())
                expected_error_count = sum(report.error_reasons.values())
            except (AttributeError, TypeError, ValueError) as error:
                raise ProfileGenerationExecutionError(
                    "eligible result generation counts are invalid"
                ) from error
            if self.skip_count != expected_skip_count or self.error_count != expected_error_count:
                raise ProfileGenerationExecutionError(
                    "eligible result generator counts do not reconcile"
                )
        else:
            if self.generation is not None or self.validated is not None:
                raise ProfileGenerationExecutionError("failed result cannot carry success payloads")
            if self.failure_phase is None or self.failure_reason is None:
                raise ProfileGenerationExecutionError("failed result requires a bounded failure")
            try:
                phase = (
                    self.failure_phase
                    if isinstance(self.failure_phase, ProfileGenerationFailurePhase)
                    else ProfileGenerationFailurePhase(self.failure_phase)
                )
                reason = (
                    self.failure_reason
                    if isinstance(self.failure_reason, ProfileGenerationFailureReason)
                    else ProfileGenerationFailureReason(self.failure_reason)
                )
            except (TypeError, ValueError) as error:
                raise ProfileGenerationExecutionError("profile generation failure is invalid") from error
            expected_reason = {
                ProfileGenerationFailurePhase.REFRESH_EXECUTION: ProfileGenerationFailureReason.REFRESH_EXECUTION_FAILED,
                ProfileGenerationFailurePhase.STAGED_BUNDLE_LOAD: ProfileGenerationFailureReason.STAGED_BUNDLE_LOAD_FAILED,
                ProfileGenerationFailurePhase.STAGE_SELECTION: ProfileGenerationFailureReason.STAGE_SELECTION_FAILED,
                ProfileGenerationFailurePhase.GENERATION: ProfileGenerationFailureReason.GENERATION_FAILED,
                ProfileGenerationFailurePhase.VALIDATION: ProfileGenerationFailureReason.VALIDATION_FAILED,
            }[phase]
            if reason is not expected_reason:
                raise ProfileGenerationExecutionError("profile generation failure phase does not match reason")
            object.__setattr__(self, "failure_phase", phase)
            object.__setattr__(self, "failure_reason", reason)
            _validate_count(self.skip_count, "skip_count", required=False)
            _validate_count(self.error_count, "error_count", required=False)
            if self.skip_count is not None or self.error_count is not None:
                raise ProfileGenerationExecutionError("failed result cannot carry generator counts")

    @property
    def publication_eligible(self) -> bool:
        return self.outcome is ProfileGenerationEnvironmentOutcome.PUBLICATION_ELIGIBLE

    @property
    def profile_bytes(self) -> bytes | None:
        return None if self.validated is None else self.validated.profile_bytes

    @property
    def gzip_bytes(self) -> bytes | None:
        return None if self.validated is None else self.validated.gzip_bytes

    @property
    def report_bytes(self) -> bytes | None:
        return None if self.validated is None else self.validated.report_bytes

    @property
    def profile_sha256(self) -> str | None:
        return _sha256_or_none(self.profile_bytes)

    @property
    def gzip_sha256(self) -> str | None:
        return _sha256_or_none(self.gzip_bytes)

    @property
    def report_sha256(self) -> str | None:
        return _sha256_or_none(self.report_bytes)

    @property
    def profile_size(self) -> int | None:
        return _size_or_none(self.profile_bytes)

    @property
    def gzip_size(self) -> int | None:
        return _size_or_none(self.gzip_bytes)

    @property
    def report_size(self) -> int | None:
        return _size_or_none(self.report_bytes)

    def to_json(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "diagnostic_total": self.diagnostic_total,
            "diagnostics": [item.to_json() for item in self.diagnostics],
            "diagnostics_omitted": self.diagnostics_omitted,
            "environment": self.environment.to_json(),
            "failure_phase": None,
            "failure_reason": None,
            "generator_counts": None,
            "outcome": self.outcome.value,
            "schema_version": self.schema_version,
            "selection": None if self.selection is None else self.selection.to_json(),
            "validated": None,
        }
        if self.publication_eligible:
            value["generator_counts"] = {
                "errors": self.error_count,
                "skips": self.skip_count,
            }
            value["validated"] = {
                "gzip_bytes": self.gzip_size,
                "gzip_sha256": self.gzip_sha256,
                "profile_bytes": self.profile_size,
                "profile_sha256": self.profile_sha256,
                "report_bytes": self.report_size,
                "report_sha256": self.report_sha256,
            }
        else:
            value["failure_phase"] = self.failure_phase.value
            value["failure_reason"] = self.failure_reason.value
        return value

    def to_bytes(self) -> bytes:
        return (
            json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")


def generate_staged_environment_profile(
    *,
    bundle_path: PathInput,
    environment: PlannedEnvironment,
    generated_at: datetime,
    profile_version: str = "1.0",
    config: ProfileGenerationConfig = DEFAULT_PROFILE_GENERATION_CONFIG,
    thresholds: ProfileGenerationStageThresholds = DEFAULT_PROFILE_GENERATION_STAGE_THRESHOLDS,
    expected_plan_sha256: str | None = None,
) -> ProfileGenerationEnvironmentResult:
    """Generate and validate exactly one staged environment profile."""

    if not isinstance(environment, PlannedEnvironment):
        raise ProfileGenerationExecutionError("environment must be a PlannedEnvironment")

    try:
        bundle = load_staged_profile_build_bundle(
            bundle_path, environment=environment, expected_plan_sha256=expected_plan_sha256
        )
    except Exception:  # noqa: BLE001 - convert all local failures to bounded output
        return _failure(
            environment=environment,
            phase=ProfileGenerationFailurePhase.STAGED_BUNDLE_LOAD,
            reason=ProfileGenerationFailureReason.STAGED_BUNDLE_LOAD_FAILED,
        )

    try:
        selection = select_profile_generation_stage(
            ratings_report=bundle.ratings_source,
            public_draft_report=bundle.public_draft_source,
            thresholds=thresholds,
        )
    except Exception:  # noqa: BLE001 - policy failures are finite and path-free
        return _failure(
            environment=environment,
            phase=ProfileGenerationFailurePhase.STAGE_SELECTION,
            reason=ProfileGenerationFailureReason.STAGE_SELECTION_FAILED,
        )

    diagnostics, total, omitted = _classify_cards(bundle.card_database, environment.set_code)

    try:
        generation = generate_set_profile(
            **bundle.generator_inputs(),
            stage=selection.stage,
            generated_at=generated_at,
            profile_version=profile_version,
            config=config,
        )
    except Exception:  # noqa: BLE001 - generation errors must not leak details
        return _failure(
            environment=environment,
            phase=ProfileGenerationFailurePhase.GENERATION,
            reason=ProfileGenerationFailureReason.GENERATION_FAILED,
            selection=selection,
            diagnostics=diagnostics,
            diagnostic_total=total,
            diagnostics_omitted=omitted,
        )

    try:
        validated = validate_profile_generation(
            generation=generation,
            set_code=environment.set_code.casefold(),
            event_format=environment.event_format.casefold(),
            stage=selection.stage.value,
        )
        if not isinstance(validated, ValidatedProfileGeneration):
            raise ProfileGenerationExecutionError("validator returned an invalid payload")
    except Exception:  # noqa: BLE001 - validator failures are finite and path-free
        return _failure(
            environment=environment,
            phase=ProfileGenerationFailurePhase.VALIDATION,
            reason=ProfileGenerationFailureReason.VALIDATION_FAILED,
            selection=selection,
            diagnostics=diagnostics,
            diagnostic_total=total,
            diagnostics_omitted=omitted,
        )

    report = generation.report
    input_sources = tuple(
        {
            "role": role,
            "name": source_report.source.name,
            "sha256": source_report.sha256 or "",
            "source_version": source_report.source_version or "",
        }
        for role, source_report in (
            ("card_database", bundle.card_metadata),
            ("seventeen_lands_ratings", bundle.ratings_source),
            ("seventeen_lands_public_drafts", bundle.public_draft_source),
        )
        if source_report is not None
    )
    try:
        return ProfileGenerationEnvironmentResult(
            environment=environment,
            outcome=ProfileGenerationEnvironmentOutcome.PUBLICATION_ELIGIBLE,
            selection=selection,
            diagnostics=diagnostics,
            diagnostic_total=total,
            diagnostics_omitted=omitted,
            skip_count=sum(report.skip_reasons.values()),
            error_count=sum(report.error_reasons.values()),
            generation=generation,
            validated=validated,
            input_sources=input_sources,
        )
    except Exception:  # noqa: BLE001 - malformed success contracts become bounded failures
        return _failure(
            environment=environment,
            phase=ProfileGenerationFailurePhase.VALIDATION,
            reason=ProfileGenerationFailureReason.VALIDATION_FAILED,
            selection=selection,
            diagnostics=diagnostics,
            diagnostic_total=total,
            diagnostics_omitted=omitted,
        )


def _failure(
    *,
    environment: PlannedEnvironment,
    phase: ProfileGenerationFailurePhase,
    reason: ProfileGenerationFailureReason,
    selection: ProfileGenerationStageSelection | None = None,
    diagnostics: tuple[ProfileGenerationDiagnostic, ...] = (),
    diagnostic_total: int = 0,
    diagnostics_omitted: int = 0,
) -> ProfileGenerationEnvironmentResult:
    return ProfileGenerationEnvironmentResult(
        environment=environment,
        outcome=ProfileGenerationEnvironmentOutcome.FAILED,
        selection=selection,
        diagnostics=diagnostics,
        diagnostic_total=diagnostic_total,
        diagnostics_omitted=diagnostics_omitted,
        failure_phase=phase,
        failure_reason=reason,
    )


def _classify_cards(
    card_database: Any,
    set_code: str,
) -> tuple[tuple[ProfileGenerationDiagnostic, ...], int, int]:
    requested_set_code = set_code.casefold()
    values: list[ProfileGenerationDiagnostic] = []
    classifier = RoleClassifier()
    try:
        cards = sorted(card_database.cards.values(), key=lambda card: (card.oracle_id or "", card.grp_id))
    except Exception:  # noqa: BLE001 - malformed loaded data is handled as a gap
        return (), 0, 0

    for card in cards:
        try:
            if card.unknown or (card.set_code is not None and card.set_code.casefold() != requested_set_code):
                continue
            candidate = card
            if card.set_code is None:
                # Keep this in sync with the generator's role compiler: cards
                # without set metadata are classified as members of the
                # requested set without mutating the loaded database.
                candidate = replace(card, set_code=requested_set_code)
            result = classifier.classify(candidate)

        except Exception:  # noqa: BLE001 - classifier failures become fixed diagnostics
            values.append(
                ProfileGenerationDiagnostic(
                    card_key=_card_key_safe(card),
                    card_name=_card_name_safe(card),
                    mechanic=None,
                    reason=CLASSIFICATION_ERROR_REASON,
                )
            )
            continue

        if result.unknown_reports:
            try:
                for report in result.unknown_reports:
                    values.append(
                        ProfileGenerationDiagnostic(
                            card_key=report.card_key,
                            card_name=report.card_name,
                            mechanic=report.mechanic,
                            reason=report.reason,
                        )
                    )
            except Exception:  # noqa: BLE001 - malformed report becomes a fixed diagnostic
                values.append(
                    ProfileGenerationDiagnostic(
                        card_key=_card_key_safe(card),
                        card_name=_card_name_safe(card),
                        mechanic=None,
                        reason=CLASSIFICATION_ERROR_REASON,
                    )
                )
        elif not result.assignments:
            values.append(
                ProfileGenerationDiagnostic(
                    card_key=result.card_key,
                    card_name=result.card_name,
                    mechanic=None,
                    reason=CLASSIFICATION_GAP_REASON,
                )
            )


    unique = tuple(sorted(set(values), key=_diagnostic_sort_key))
    total = len(unique)
    retained = unique[:_MAX_DIAGNOSTICS]
    return retained, total, total - len(retained)

def _diagnostic_sort_key(value: ProfileGenerationDiagnostic) -> tuple[str, ...]:
    # Case-insensitive ordering is operator-friendly; raw values make ties total.
    folded = (
        value.card_key.casefold(),
        "" if value.card_name is None else value.card_name.casefold(),
        "" if value.mechanic is None else value.mechanic.casefold(),
        value.reason.casefold(),
    )
    raw = (
        value.card_key,
        "" if value.card_name is None else value.card_name,
        "" if value.mechanic is None else value.mechanic,
        value.reason,
    )
    return folded + raw


def _card_key_safe(card: Any) -> str:
    oracle_id = getattr(card, "oracle_id", None)
    if isinstance(oracle_id, str) and oracle_id:
        return f"oracle_id:{oracle_id}".casefold()
    set_code = getattr(card, "set_code", None)
    collector_number = getattr(card, "collector_number", None)
    if isinstance(set_code, str) and set_code and isinstance(collector_number, str) and collector_number:
        return f"set:{set_code}:{collector_number}".casefold()
    arena_id = getattr(card, "arena_id", None)
    if isinstance(arena_id, int) and not isinstance(arena_id, bool):
        return f"arena_id:{arena_id}".casefold()
    grp_id = getattr(card, "grp_id", None)
    return f"grp_id:{grp_id}".casefold() if isinstance(grp_id, int) else "card:unknown"


def _card_name_safe(card: Any) -> str | None:
    name = getattr(card, "name", None)
    return name if isinstance(name, str) else None


def _bounded_required(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileGenerationExecutionError(f"diagnostic {field_name} is invalid")
    return value.strip()[:_MAX_DIAGNOSTIC_FIELD_LENGTH]


def _bounded_optional(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_MAX_DIAGNOSTIC_FIELD_LENGTH]


def _validate_count(value: Any, field_name: str, *, required: bool) -> None:
    if value is None:
        if required:
            raise ProfileGenerationExecutionError(f"{field_name} is required")
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProfileGenerationExecutionError(f"{field_name} is invalid")


def _sha256_or_none(value: bytes | None) -> str | None:
    return None if value is None else hashlib.sha256(value).hexdigest()


def _size_or_none(value: bytes | None) -> int | None:
    return None if value is None else len(value)


__all__ = [
    "CLASSIFICATION_ERROR_REASON",
    "CLASSIFICATION_GAP_REASON",
    "PROFILE_GENERATION_EXECUTION_SCHEMA_VERSION",
    "ProfileGenerationDiagnostic",
    "ProfileGenerationEnvironmentOutcome",
    "ProfileGenerationEnvironmentResult",
    "ProfileGenerationExecutionError",
    "ProfileGenerationFailurePhase",
    "ProfileGenerationFailureReason",
    "generate_staged_environment_profile",
]
