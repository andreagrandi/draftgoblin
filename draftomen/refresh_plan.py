"""Deterministic plans for refreshing set profiles.

The 17Lands expansion endpoint is an eligibility inventory only.  Rotation and
lifecycle are supplied separately by an operator from an authoritative Arena
schedule; this module deliberately does not infer either one.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from draftomen.seventeen import (
    HTTP_TIMEOUT_SECONDS,
    SEVENTEEN_LANDS_EXPANSIONS_ENDPOINT,
    SeventeenLandsError,
    SeventeenLandsExpansionInventory,
    fetch_17lands_expansion_inventory,
    parse_17lands_expansion_inventory,
)

REFRESH_PLAN_SCHEMA_VERSION = 1
LIFECYCLE_STAGES = ("active", "mature", "historical")
SelectionMode = Literal["manual", "active", "history"]


class RefreshPlanError(ValueError):
    """Raised when a refresh plan cannot be built from its required inputs."""


@dataclass(frozen=True, slots=True)
class LifecycleMetadata:
    """Operator-supplied lifecycle classification and its provenance.

    ``classifications`` contains only normalized set codes with one of the
    explicitly supplied lifecycle stages.  Missing metadata is represented by
    diagnostics instead of silently turning an eligible expansion into an
    inferred active or historical environment.
    """

    provider: str
    source_url: str
    version: str
    classifications: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        provider = _clean_text(self.provider) or ""
        source_url = _clean_text(self.source_url) or ""
        version = _clean_text(self.version) or ""
        normalized: dict[str, str] = {}
        for code, stage in self.classifications:
            normalized_code = _normalize_set_code(code)
            normalized_stage = _clean_text(stage)
            if normalized_code is None or normalized_stage not in LIFECYCLE_STAGES:
                continue
            normalized[normalized_code] = normalized_stage
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "classifications", tuple(sorted(normalized.items())))
        object.__setattr__(self, "diagnostics", tuple(sorted(set(self.diagnostics))))

    def stage_for(self, set_code: str) -> str | None:
        """Return the explicit lifecycle stage for one normalized set code."""

        normalized = _normalize_set_code(set_code)
        if normalized is None:
            return None
        for code, stage in self.classifications:
            if code == normalized:
                return stage
        return None

    def to_json(self) -> dict[str, Any]:
        """Return canonical JSON-compatible lifecycle provenance."""

        return {
            "provider": self.provider,
            "source_url": self.source_url,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class PlannedEnvironment:
    """One selected set and event-format identity with selection rationale."""

    set_code: str
    event_format: str
    lifecycle: str | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        set_code = _normalize_set_code(self.set_code)
        event_format = _normalize_event_format(self.event_format)
        if set_code is None:
            raise ValueError("planned environment set code must be non-empty")
        if event_format is None:
            raise ValueError("planned environment event format must be non-empty")
        lifecycle = _clean_text(self.lifecycle)
        if lifecycle not in LIFECYCLE_STAGES:
            lifecycle = None
        reasons = tuple(sorted({reason.strip() for reason in self.reasons if reason.strip()}))
        if not reasons:
            raise ValueError("planned environment must have at least one reason")
        object.__setattr__(self, "set_code", set_code)
        object.__setattr__(self, "event_format", event_format)
        object.__setattr__(self, "lifecycle", lifecycle)
        object.__setattr__(self, "reasons", reasons)

    def to_json(self) -> dict[str, Any]:
        return {
            "event_format": self.event_format,
            "lifecycle": self.lifecycle,
            "reasons": list(self.reasons),
            "set_code": self.set_code,
        }


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """A canonical, profile-generation-free refresh selection plan."""

    selection_mode: SelectionMode
    event_format: str
    environments: tuple[PlannedEnvironment, ...]
    inventory_source_url: str
    inventory_payload_digest: str
    lifecycle: LifecycleMetadata
    diagnostics: tuple[str, ...] = ()
    selection_set_code: str | None = None
    max_environments: int | None = None
    schema_version: int = REFRESH_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REFRESH_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported refresh plan schema version: {self.schema_version!r}")
        if self.selection_mode not in ("manual", "active", "history"):
            raise ValueError("refresh plan selection mode must be manual, active, or history")
        event_format = _normalize_event_format(self.event_format)
        if event_format is None:
            raise ValueError("refresh plan event format must be non-empty")
        source_url = _clean_text(self.inventory_source_url)
        digest = _clean_text(self.inventory_payload_digest)
        if source_url is None or digest is None:
            raise ValueError("refresh plan inventory provenance must be non-empty")
        if self.selection_mode == "manual" and _normalize_set_code(self.selection_set_code) is None:
            raise ValueError("manual refresh plans require a set code")
        if self.selection_mode == "history" and (
            not isinstance(self.max_environments, int) or isinstance(self.max_environments, bool)
            or self.max_environments < 1
        ):
            raise ValueError("history refresh plans require a positive max environment count")
        environments = tuple(
            sorted(
                self.environments,
                key=lambda item: (item.set_code, item.event_format, item.lifecycle or "", item.reasons),
            )
        )
        if any(not isinstance(item, PlannedEnvironment) for item in environments):
            raise TypeError("refresh plan environments must be PlannedEnvironment values")
        diagnostics = tuple(sorted(set(diagnostic for diagnostic in self.diagnostics if diagnostic)))
        object.__setattr__(self, "event_format", event_format)
        object.__setattr__(self, "inventory_source_url", source_url)
        object.__setattr__(self, "inventory_payload_digest", digest)
        object.__setattr__(self, "selection_set_code", _normalize_set_code(self.selection_set_code))
        object.__setattr__(self, "environments", environments)
        object.__setattr__(self, "diagnostics", diagnostics)

    def to_json(self) -> dict[str, Any]:
        """Return the one supported plan schema as JSON-compatible data."""

        selection: dict[str, Any] = {"mode": self.selection_mode}
        if self.selection_set_code is not None:
            selection["set_code"] = self.selection_set_code
        if self.max_environments is not None:
            selection["max_environments"] = self.max_environments
        return {
            "diagnostics": list(self.diagnostics),
            "environments": [environment.to_json() for environment in self.environments],
            "event_format": self.event_format,
            "inventory": {
                "source_payload_digest": self.inventory_payload_digest,
                "source_url": self.inventory_source_url,
            },
            "lifecycle": self.lifecycle.to_json(),
            "schema_version": self.schema_version,
            "selection": selection,
        }

    def to_bytes(self) -> bytes:
        """Serialize with sorted keys, compact separators, and one newline."""

        return (json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )


def parse_lifecycle_metadata(
    payload: Any,
    *,
    source_url: str = "",
) -> LifecycleMetadata:
    """Normalize a lifecycle document without making date or rotation claims.

    The accepted document uses ``provider``, ``source_url``, and ``version``
    provenance fields, plus either stage lists (``active``, ``mature``, and
    ``historical``) or records under ``environments``/``records``.  Invalid
    portions produce stable diagnostics while valid records survive.
    """

    diagnostics: list[str] = []
    if not isinstance(payload, Mapping):
        return LifecycleMetadata(
            provider="",
            source_url=source_url,
            version="",
            diagnostics=("lifecycle-document-not-object",),
        )

    provider = _metadata_text(payload, "provider", diagnostics)
    document_source_url = _metadata_text(payload, "source_url", diagnostics)
    if not document_source_url:
        document_source_url = _clean_text(source_url) or ""
        if not document_source_url:
            diagnostics.append("lifecycle-source-url-missing")
    version = _metadata_text(payload, "version", diagnostics)

    classifications: dict[str, str] = {}
    for stage in LIFECYCLE_STAGES:
        if stage not in payload:
            continue
        value = payload[stage]
        if not isinstance(value, list):
            diagnostics.append(f"lifecycle-{stage}-not-list")
            continue
        for index, entry in enumerate(value):
            code = _record_code(entry)
            if code is None:
                diagnostics.append(f"lifecycle-{stage}-entry-{index}-invalid")
                continue
            _add_classification(code, stage, classifications, diagnostics)

    for field_name in ("environments", "records"):
        records = payload.get(field_name)
        if records is None:
            continue
        if not isinstance(records, list):
            diagnostics.append(f"lifecycle-{field_name}-not-list")
            continue
        for index, entry in enumerate(records):
            if not isinstance(entry, Mapping):
                diagnostics.append(f"lifecycle-{field_name}-entry-{index}-invalid")
                continue
            code = _record_code(entry)
            stage = _clean_text(entry.get("lifecycle") or entry.get("stage"))
            if code is None or stage not in LIFECYCLE_STAGES:
                diagnostics.append(f"lifecycle-{field_name}-entry-{index}-invalid")
                continue
            _add_classification(code, stage, classifications, diagnostics)

    if not any(stage in payload for stage in LIFECYCLE_STAGES) and not any(
        field in payload for field in ("environments", "records")
    ):
        diagnostics.append("lifecycle-classifications-missing")

    return LifecycleMetadata(
        provider=provider,
        source_url=document_source_url,
        version=version,
        classifications=tuple(classifications.items()),
        diagnostics=tuple(diagnostics),
    )


def build_refresh_plan(
    inventory: SeventeenLandsExpansionInventory,
    lifecycle: LifecycleMetadata,
    *,
    event_format: str,
    selection_mode: SelectionMode,
    set_code: str | None = None,
    max_environments: int | None = None,
) -> RefreshPlan:
    """Build a deterministic manual, active, or bounded historical plan."""

    if not isinstance(inventory, SeventeenLandsExpansionInventory):
        raise TypeError("inventory must be a SeventeenLandsExpansionInventory")
    if not isinstance(lifecycle, LifecycleMetadata):
        raise TypeError("lifecycle must be a LifecycleMetadata")
    normalized_format = _normalize_event_format(event_format)
    if normalized_format is None:
        raise RefreshPlanError("--event-format must be non-empty")
    if selection_mode not in ("manual", "active", "history"):
        raise RefreshPlanError("selection must be manual, active, or history")
    normalized_set = _normalize_set_code(set_code)
    if selection_mode == "manual" and normalized_set is None:
        raise RefreshPlanError("manual selection requires --set-code")
    if selection_mode != "manual" and normalized_set is not None:
        raise RefreshPlanError("--set-code is only valid for manual selection")
    if selection_mode == "history" and (
        not isinstance(max_environments, int) or isinstance(max_environments, bool) or max_environments < 1
    ):
        raise RefreshPlanError("history selection requires --max-environments >= 1")
    if selection_mode != "history" and max_environments is not None:
        raise RefreshPlanError("--max-environments is only valid for history selection")

    diagnostics = [
        (
            f"inventory:{item.reason}:entry={item.entry}:{item.detail}"
            if item.entry is not None
            else f"inventory:{item.reason}:{item.detail}"
        )
        for item in inventory.diagnostics
    ]
    diagnostics.extend(f"lifecycle:{item}" for item in lifecycle.diagnostics)
    known_codes = tuple(sorted(inventory.expansion_codes))
    statuses = dict(lifecycle.classifications)
    for code in known_codes:
        if code not in statuses:
            diagnostics.append(f"lifecycle-missing-for-inventory:{code}")
    for code in sorted(statuses):
        if code not in known_codes:
            diagnostics.append(f"lifecycle-code-not-in-inventory:{code}")

    selected_codes: list[str] = []
    reasons: dict[str, tuple[str, ...]] = {}
    if selection_mode == "manual":
        assert normalized_set is not None
        if normalized_set in known_codes:
            selected_codes.append(normalized_set)
            reasons[normalized_set] = ("manual",)
        else:
            diagnostics.append(f"manual-set-not-in-inventory:{normalized_set}")
    elif selection_mode == "active":
        selected_codes = [code for code in known_codes if statuses.get(code) == "active"]
        reasons = {code: ("active",) for code in selected_codes}
    else:
        historical = [code for code in known_codes if statuses.get(code) == "historical"]
        assert max_environments is not None
        selected_codes = historical[:max_environments]
        reasons = {code: ("historical",) for code in selected_codes}
        if len(historical) > max_environments:
            diagnostics.append(
                f"history-bounded:{max_environments}:excluded={','.join(historical[max_environments:])}"
            )

    if not selected_codes:
        if selection_mode == "manual":
            assert normalized_set is not None
            message = (
                f"manual selection set code {normalized_set!r} is not present in the "
                "17Lands inventory"
            )
        else:
            stage = "active" if selection_mode == "active" else "historical"
            message = (
                f"{selection_mode} selection matched no environments; verify that "
                f"lifecycle metadata classifies an inventory set as {stage}"
            )
        if diagnostics:
            message += f"; diagnostics: {', '.join(sorted(set(diagnostics)))}"
        raise RefreshPlanError(message)

    environments = tuple(
        PlannedEnvironment(
            set_code=code,
            event_format=normalized_format,
            lifecycle=statuses.get(code),
            reasons=reasons[code],
        )
        for code in selected_codes
    )
    return RefreshPlan(
        selection_mode=selection_mode,
        event_format=normalized_format,
        environments=environments,
        inventory_source_url=inventory.source_url,
        inventory_payload_digest=inventory.source_payload_digest,
        lifecycle=lifecycle,
        diagnostics=tuple(diagnostics),
        selection_set_code=normalized_set,
        max_environments=max_environments,
    )


def load_17lands_inventory_file(path: str | os.PathLike[str]) -> SeventeenLandsExpansionInventory:
    """Load the exact raw JSON list accepted by ``/data/expansions``."""

    input_path = Path(path)
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RefreshPlanError(f"could not read inventory file {input_path}: {error}") from error
    try:
        return parse_17lands_expansion_inventory(
            payload,
            source_url=SEVENTEEN_LANDS_EXPANSIONS_ENDPOINT,
        )
    except SeventeenLandsError as error:
        raise RefreshPlanError(f"invalid 17Lands inventory file {input_path}: {error}") from error


def load_lifecycle_file(path: str | os.PathLike[str]) -> LifecycleMetadata:
    """Load lifecycle JSON, retaining malformed input as actionable diagnostics."""

    input_path = Path(path)
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return LifecycleMetadata(
            provider="",
            source_url="",
            version="",
            diagnostics=(f"lifecycle-file-read-failed:{type(error).__name__}",),
        )
    return parse_lifecycle_metadata(payload)


def fetch_lifecycle_metadata(url: str, *, timeout_seconds: int = HTTP_TIMEOUT_SECONDS) -> LifecycleMetadata:
    """Fetch one operator-supplied lifecycle JSON document."""

    normalized_url = _clean_text(url) or ""
    try:
        request = urllib.request.Request(
            normalized_url,
            headers={"User-Agent": "draftomen-refresh-plan"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as error:
        return LifecycleMetadata(
            provider="",
            source_url="",
            version="",
            diagnostics=(f"lifecycle-fetch-failed:{type(error).__name__}",),
        )
    except ValueError as error:
        raise RefreshPlanError("lifecycle URL must be an absolute URL") from error
    return parse_lifecycle_metadata(payload, source_url=normalized_url)


def discover_17lands_inventory() -> SeventeenLandsExpansionInventory:
    """Use the network-backed 17Lands expansion inventory default."""

    return fetch_17lands_expansion_inventory()


def write_refresh_plan(path: str | os.PathLike[str], plan: RefreshPlan) -> Path:
    """Atomically write canonical plan bytes and return the destination."""

    if not isinstance(plan, RefreshPlan):
        raise TypeError("plan must be a RefreshPlan")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(plan.to_bytes())
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    except OSError as error:
        raise RefreshPlanError(f"could not write refresh plan {destination}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return destination


def _metadata_text(payload: Mapping[str, Any], name: str, diagnostics: list[str]) -> str:
    value = payload.get(name)
    text = _clean_text(value)
    if text is None:
        diagnostics.append(f"lifecycle-{name}-missing")
        return ""
    if not isinstance(value, str):
        diagnostics.append(f"lifecycle-{name}-invalid")
        return ""
    return text


def _record_code(value: Any) -> str | None:
    if isinstance(value, str):
        return _normalize_set_code(value)
    if isinstance(value, Mapping):
        return _normalize_set_code(value.get("set_code") or value.get("code"))
    return None


def _add_classification(
    code: str,
    stage: str,
    classifications: dict[str, str],
    diagnostics: list[str],
) -> None:
    previous = classifications.get(code)
    if previous is None:
        classifications[code] = stage
    elif previous == stage:
        diagnostics.append(f"lifecycle-duplicate:{code}:{stage}")
    else:
        diagnostics.append(f"lifecycle-conflict:{code}:{previous},{stage}")


def _normalize_set_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().upper().split())
    return normalized or None


def _normalize_event_format(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None



__all__ = [
    "LIFECYCLE_STAGES",
    "REFRESH_PLAN_SCHEMA_VERSION",
    "LifecycleMetadata",
    "PlannedEnvironment",
    "RefreshPlan",
    "RefreshPlanError",
    "build_refresh_plan",
    "discover_17lands_inventory",
    "fetch_lifecycle_metadata",
    "load_17lands_inventory_file",
    "load_lifecycle_file",
    "parse_lifecycle_metadata",
    "write_refresh_plan",
]
