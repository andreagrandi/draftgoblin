from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import pytest

from draftomen.refresh_plan import (
    LifecycleMetadata,
    RefreshPlan,
    RefreshPlanError,
    build_refresh_plan,
    load_17lands_inventory_file,
    load_lifecycle_file,
    load_refresh_plan,
    parse_lifecycle_metadata,
    write_refresh_plan,
)
from draftomen.seventeen import SEVENTEEN_LANDS_EXPANSIONS_ENDPOINT


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "refresh-plan"


def _inputs() -> tuple[object, LifecycleMetadata]:
    inventory = load_17lands_inventory_file(FIXTURE_DIR / "expansions.json")
    lifecycle = load_lifecycle_file(FIXTURE_DIR / "lifecycle.json")
    return inventory, lifecycle


def _plan() -> RefreshPlan:
    inventory, lifecycle = _inputs()
    return build_refresh_plan(
        inventory,
        lifecycle,
        event_format="PremierDraft",
        selection_mode="manual",
        set_code="NEW",
    )


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


def test_refresh_plan_decoding_round_trips_canonical_bytes_and_file(tmp_path: Path) -> None:
    plan = _plan()

    assert RefreshPlan.from_json(plan.to_json()) == plan
    assert RefreshPlan.from_bytes(plan.to_bytes()) == plan

    plan_path = tmp_path / "refresh-plan.json"
    plan_path.write_bytes(plan.to_bytes())
    assert load_refresh_plan(plan_path) == plan


def test_refresh_plan_decoder_rejects_missing_unknown_and_future_fields() -> None:
    canonical = _plan().to_json()
    mutations = []

    missing = dict(canonical)
    del missing["inventory"]
    mutations.append(missing)

    unknown = dict(canonical)
    unknown["future_field"] = True
    mutations.append(unknown)

    future = dict(canonical)
    future["schema_version"] = 2
    mutations.append(future)

    for value in mutations:
        with pytest.raises(RefreshPlanError):
            RefreshPlan.from_json(value)


@pytest.mark.parametrize("unsafe", [".", "..", "../escape", r"..\escape", "NEW\x00"])
def test_refresh_plan_decoder_rejects_unsafe_path_components(unsafe: str) -> None:
    canonical = _plan().to_json()
    mutations = []

    event_format = json.loads(json.dumps(canonical))
    event_format["event_format"] = unsafe
    mutations.append(event_format)

    environment_format = json.loads(json.dumps(canonical))
    environment_format["environments"][0]["event_format"] = unsafe
    mutations.append(environment_format)

    environment_set = json.loads(json.dumps(canonical))
    environment_set["environments"][0]["set_code"] = unsafe
    mutations.append(environment_set)

    selection_set = json.loads(json.dumps(canonical))
    selection_set["selection"]["set_code"] = unsafe
    mutations.append(selection_set)

    for value in mutations:
        with pytest.raises(RefreshPlanError):
            RefreshPlan.from_json(value)


def test_refresh_plan_decoder_rejects_duplicate_identities_and_invalid_order() -> None:
    inventory, _ = _inputs()
    lifecycle = LifecycleMetadata(
        provider="Arena schedule",
        source_url="https://schedule.example.test/arena.json",
        version="v1",
        classifications=(("NEW", "active"), ("MATURE", "active")),
    )
    plan = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="PremierDraft",
        selection_mode="active",
    )

    duplicate = json.loads(json.dumps(plan.to_json()))
    duplicate["environments"].append(dict(duplicate["environments"][0]))
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_json(duplicate)

    reordered = json.loads(json.dumps(plan.to_json()))
    reordered["environments"].reverse()
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_json(reordered)

    reordered_diagnostics = json.loads(json.dumps(plan.to_json()))
    reordered_diagnostics["diagnostics"].reverse()
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_json(reordered_diagnostics)


def test_refresh_plan_decoder_rejects_invalid_shapes_and_noncanonical_bytes() -> None:
    plan = _plan()

    missing_reason = json.loads(json.dumps(plan.to_json()))
    missing_reason["environments"][0]["reasons"] = []
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_json(missing_reason)

    invalid_selection = json.loads(json.dumps(plan.to_json()))
    invalid_selection["selection"] = {"mode": "manual"}
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_json(invalid_selection)

    pretty = json.dumps(plan.to_json(), indent=2).encode("utf-8")
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_bytes(pretty)

    duplicate_key = plan.to_bytes().replace(
        b'"schema_version":1',
        b'"schema_version":1,"schema_version":1',
        1,
    )
    with pytest.raises(RefreshPlanError):
        RefreshPlan.from_bytes(duplicate_key)


def test_load_refresh_plan_errors_do_not_retain_supplied_path(tmp_path: Path) -> None:
    private_path = tmp_path / "private-refresh-plan-sentinel.json"

    with pytest.raises(RefreshPlanError) as error:
        load_refresh_plan(private_path)

    assert str(tmp_path) not in str(error.value)
    assert str(private_path) not in str(error.value)


def test_constructed_plan_bounds_oversized_and_control_diagnostics() -> None:
    noisy = [f"diagnostic-{index:03d}" for index in range(300)]
    noisy.append("history-bounded:1:excluded=" + ",".join(f"SET{index}" for index in range(200)))
    noisy.append("inventory:bad\tentry\nwith-controls")
    plan = replace(_plan(), diagnostics=tuple(noisy))

    assert len(plan.diagnostics) <= 256
    assert all(len(diagnostic) <= 512 for diagnostic in plan.diagnostics)
    assert all(
        ord(character) >= 32 and ord(character) != 127
        for diagnostic in plan.diagnostics
        for character in diagnostic
    )
    assert RefreshPlan.from_bytes(plan.to_bytes()) == plan


def test_write_refresh_plan_output_is_always_loadable(tmp_path: Path) -> None:
    plan = replace(_plan(), diagnostics=("inventory:bad\tentry", "x" * 600))
    destination = tmp_path / "plan.json"

    write_refresh_plan(destination, plan)

    assert load_refresh_plan(destination) == plan


def test_plan_decoder_rejects_float_schema_version() -> None:
    value = _plan().to_json()
    value["schema_version"] = 1.0

    with pytest.raises(RefreshPlanError, match="unsupported schema version"):
        RefreshPlan.from_json(value)


def test_selection_fields_are_rejected_outside_their_mode() -> None:
    inventory, lifecycle = _inputs()
    active = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="QuickDraft",
        selection_mode="active",
    )

    with pytest.raises(ValueError, match="only manual refresh plans accept a selection set code"):
        replace(active, selection_set_code="NEW")
    with pytest.raises(ValueError, match="only history refresh plans accept a max environment count"):
        replace(active, max_environments=2)


def test_plan_preserves_caller_lifecycle_and_serializes_projection() -> None:
    inventory, lifecycle = _inputs()
    plan = build_refresh_plan(
        inventory,
        lifecycle,
        event_format="QuickDraft",
        selection_mode="active",
    )
    plan_sets = {environment.set_code for environment in plan.environments}

    assert {code for code, _ in plan.lifecycle.classifications} - plan_sets

    reloaded = RefreshPlan.from_bytes(plan.to_bytes())

    assert reloaded == plan
    assert {code for code, _ in reloaded.lifecycle.classifications} <= plan_sets
    assert reloaded.to_json() == plan.to_json()
