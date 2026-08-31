from __future__ import annotations

import json
from pathlib import Path
import pytest

from draftomen.refresh_plan import (
    LifecycleMetadata,
    RefreshPlanError,
    build_refresh_plan,
    load_17lands_inventory_file,
    load_lifecycle_file,
    parse_lifecycle_metadata,
)
from draftomen.seventeen import SEVENTEEN_LANDS_EXPANSIONS_ENDPOINT

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "refresh-plan"


def _inputs() -> tuple[object, LifecycleMetadata]:
    inventory = load_17lands_inventory_file(FIXTURE_DIR / "expansions.json")
    lifecycle = load_lifecycle_file(FIXTURE_DIR / "lifecycle.json")
    return inventory, lifecycle


def test_lifecycle_metadata_keeps_valid_records_and_reports_bad_records() -> None:
    metadata = parse_lifecycle_metadata(
        {
            "provider": "Arena schedule",
            "source_url": "https://schedule.example.test/a.json",
            "version": "v1",
            "active": ["new", "NEW", None],
            "historical": [{"set_code": "OLD"}, {"set_code": "UNKNOWN"}],
            "records": [{"set_code": "MATURE", "lifecycle": "mature"}, {"code": "BAD"}],
        }
    )

    assert metadata.classifications == (("MATURE", "mature"), ("NEW", "active"), ("OLD", "historical"), ("UNKNOWN", "historical"))
    assert "lifecycle-duplicate:NEW:active" in metadata.diagnostics
    assert "lifecycle-active-entry-2-invalid" in metadata.diagnostics
    assert metadata.to_json() == {
        "provider": "Arena schedule",
        "source_url": "https://schedule.example.test/a.json",
        "version": "v1",
    }


def test_active_and_bounded_history_plans_are_deterministic() -> None:
    inventory, lifecycle = _inputs()

    active = build_refresh_plan(
        inventory,
        lifecycle,
        event_format=" PremierDraft ",
        selection_mode="active",
    )
    history = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="PremierDraft",
        selection_mode="history",
        max_environments=1,
    )

    assert [entry.set_code for entry in active.environments] == ["NEW"]
    assert [entry.set_code for entry in history.environments] == ["OLD"]
    assert active.environments[0].event_format == "premierdraft"
    assert history.to_bytes().endswith(b"\n")
    assert json.loads(history.to_bytes()) ["selection"] == {
        "mode": "history",
        "max_environments": 1,
    }


def test_manual_unknown_set_is_rejected_with_actionable_error() -> None:
    inventory, lifecycle = _inputs()

    with pytest.raises(RefreshPlanError, match="NOT-IN-17LANDS.*not present.*17Lands inventory"):
        build_refresh_plan(
            inventory,
            lifecycle,
            event_format="QuickDraft",
            selection_mode="manual",
            set_code="NOT-IN-17LANDS",
        )


@pytest.mark.parametrize(
    ("selection_mode", "max_environments", "expected_stage"),
    [
        ("active", None, "active"),
        ("history", 1, "historical"),
    ],
)
def test_automatic_selection_without_matches_is_rejected(
    selection_mode: str,
    max_environments: int | None,
    expected_stage: str,
) -> None:
    inventory, _ = _inputs()
    lifecycle = LifecycleMetadata(
        provider="Arena schedule",
        source_url="https://schedule.example.test/arena.json",
        version="2026-08-30",
    )

    with pytest.raises(RefreshPlanError, match=f"{selection_mode} selection matched no environments.*{expected_stage}"):
        build_refresh_plan(
            inventory,
            lifecycle,
            event_format="QuickDraft",
            selection_mode=selection_mode,
            max_environments=max_environments,
        )


def test_valid_automatic_selection_keeps_unrelated_diagnostics() -> None:
    inventory, lifecycle = _inputs()
    plan = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="QuickDraft",
        selection_mode="active",
    )

    assert [entry.set_code for entry in plan.environments] == ["NEW"]
    assert any(diagnostic.startswith("inventory:malformed-entry:") for diagnostic in plan.diagnostics)


def test_offline_inventory_plan_uses_canonical_source_url_without_local_path() -> None:
    inventory, lifecycle = _inputs()

    plan = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="PremierDraft",
        selection_mode="manual",
        set_code="NEW",
    )

    assert inventory.source_url == SEVENTEEN_LANDS_EXPANSIONS_ENDPOINT
    serialized = plan.to_bytes().decode("utf-8")
    assert f'"source_url":"{SEVENTEEN_LANDS_EXPANSIONS_ENDPOINT}"' in serialized
    assert str(FIXTURE_DIR) not in serialized
    assert "file://" not in serialized


def test_lifecycle_file_provenance_is_path_free_when_url_is_missing_or_unreadable(
    tmp_path: Path,
) -> None:
    missing_url = tmp_path / "missing-url.json"
    missing_url.write_text(json.dumps({"active": ["NEW"]}), encoding="utf-8")
    metadata = load_lifecycle_file(missing_url)
    assert metadata.source_url == ""
    assert "lifecycle-source-url-missing" in metadata.diagnostics
    assert str(tmp_path) not in json.dumps(metadata.to_json())

    unreadable = load_lifecycle_file(tmp_path / "unreadable.json")
    assert unreadable.source_url == ""
    assert unreadable.diagnostics == ("lifecycle-file-read-failed:FileNotFoundError",)
    assert str(tmp_path) not in json.dumps(unreadable.to_json())


def test_inventory_diagnostics_retain_normalized_entries_in_plan() -> None:
    inventory, lifecycle = _inputs()
    plan = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="PremierDraft",
        selection_mode="manual",
        set_code="NEW",
    )

    assert (
        "inventory:duplicate-entry:entry=NEW:normalized expansion code already present"
        in plan.diagnostics
    )
    assert "inventory:malformed-entry:entry=:expected a non-empty string" in plan.diagnostics
