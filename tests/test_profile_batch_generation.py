from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

import draftomen.profile_batch_generation as batch
import draftomen.profile_refresh_execution as refresh
from draftomen.refresh_plan import LifecycleMetadata, PlannedEnvironment, RefreshPlan

from tests.test_profile_generation_execution import _bundle


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profile-generation"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _environment(set_code: str, *, lifecycle: str | None = "active") -> PlannedEnvironment:
    return PlannedEnvironment(
        set_code=set_code,
        event_format="quickdraft",
        lifecycle=lifecycle,
        reasons=("batch-test",),
    )


def _plan(environments: tuple[PlannedEnvironment, ...]) -> RefreshPlan:
    return RefreshPlan(
        selection_mode="history",
        event_format="quickdraft",
        environments=environments,
        inventory_source_url="https://inventory.example.test/sets.json",
        inventory_payload_digest="a" * 64,
        lifecycle=LifecycleMetadata(
            provider="fixture",
            source_url="https://lifecycle.example.test/sets.json",
            version="fixture-v1",
        ),
        max_environments=len(environments),
    )


def _acquired_for_environment(environment: PlannedEnvironment, tmp_path: Path):
    set_code = environment.set_code.casefold()
    drafts = set_code == "mature"
    acquired = _bundle(
        environment,
        ratings=set_code != "meta",
        drafts=drafts,
        tmp_path=tmp_path,
    )
    if not drafts:
        return acquired
    assert acquired.bundle is not None
    assert acquired.bundle.public_drafts is not None
    assert acquired.public_draft_source is not None
    draft_path = tmp_path / f"{environment.set_code}-draft-input.csv"
    payload = (FIXTURE_DIR / "mature-data.csv").read_bytes().replace(
        b"TST,", f"{environment.set_code},".encode()
    )
    draft_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    source = replace(
        acquired.bundle.public_drafts.sources[0],
        path=draft_path,
        sha256=digest,
    )
    manifest = replace(acquired.bundle.public_drafts, sources=(source,))
    report = replace(acquired.public_draft_source, sha256=digest, content_bytes=len(payload))
    bundle = replace(acquired.bundle, public_drafts=manifest, public_draft_source=report)
    return replace(acquired, bundle=bundle, public_draft_source=report)


def _stage_batch(
    tmp_path: Path,
    environments: tuple[PlannedEnvironment, ...],
    *,
    failed_set: str | None = None,
    plan: RefreshPlan | None = None,
) -> tuple[RefreshPlan, Path]:
    plan = _plan(environments) if plan is None else plan
    failed_code = None if failed_set is None else failed_set.casefold()
    acquired = {
        environment.set_code: _acquired_for_environment(environment, tmp_path)
        for environment in environments
        if environment.set_code.casefold() != failed_code
    }

    def acquire(**kwargs: object):
        environment = kwargs["environment"]
        assert isinstance(environment, PlannedEnvironment)
        if environment.set_code.casefold() == failed_code:
            raise RuntimeError("fixture acquisition failure with local secret /tmp/fixture-secret")
        return acquired[environment.set_code]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(refresh, "acquire_profile_build_bundle", acquire)
        result = refresh.execute_profile_refresh_plan(
            plan=plan,
            cache=refresh.ProfileInputCache(
                tmp_path / "cache",
                policy=refresh.DEFAULT_PROFILE_REFRESH_CACHE_POLICY,
            ),
            output_dir=tmp_path / "staged",
            offline=True,
            clock=lambda: NOW,
        )
    assert result.plan_sha256 == hashlib.sha256(plan.to_bytes()).hexdigest()
    return plan, tmp_path / "staged"


def test_batch_report_has_versions_sources_counts_and_artifact_hashes(tmp_path: Path) -> None:
    mature = _environment("MATURE")
    plan, staged = _stage_batch(tmp_path, (mature,))

    result = batch.generate_staged_profile_batch(
        plan=plan,
        staged_dir=staged,
        generated_at=NOW,
    )

    report = result.to_json()
    assert report["plan_sha256"] == hashlib.sha256(plan.to_bytes()).hexdigest()
    assert report["selection_mode"] == "history"
    assert report["schema_version"] == batch.PROFILE_BATCH_REPORT_SCHEMA_VERSION
    assert report["versions"]["generator_version"]
    assert report["versions"]["profile_generation_schema_version"]
    assert report["versions"]["profile_generation_execution_schema_version"]
    assert report["versions"]["set_profile_schema_version"]
    assert report["versions"]["public_dump_manifest_schema_version"]
    assert report["versions"]["statistics_version"]
    assert report["counts"] == {"failed": 0, "planned": 1, "publication_eligible": 1}

    environment = report["environments"][0]
    assert environment["outcome"] == "publication-eligible"
    assert environment["selection"]["stage"] == "mature"
    assert environment["sources"]
    assert {"name", "sha256", "attribution", "license"} <= set(environment["sources"][0])
    assert environment["samples"] is not None
    assert "card_games" in environment and "pair_games" in environment
    assert "skip_count" in environment and "error_count" in environment
    for artifact in ("profile", "gzip", "report"):
        assert len(environment["artifacts"][artifact]["sha256"]) == 64
        assert environment["artifacts"][artifact]["bytes"] > 0


def test_one_invalid_environment_does_not_discard_valid_siblings(tmp_path: Path) -> None:
    environments = (_environment("EARLY"), _environment("FAIL"), _environment("MATURE"))
    plan, staged = _stage_batch(tmp_path, environments, failed_set="FAIL")

    result = batch.generate_staged_profile_batch(
        plan=plan,
        staged_dir=staged,
        generated_at=NOW,
    )

    assert result.succeeded is False
    assert result.reconciled_counts == {"failed": 1, "planned": 3, "publication_eligible": 2}
    assert [item.environment.set_code for item in result.environments] == ["EARLY", "FAIL", "MATURE"]
    assert [item.publication_eligible for item in result.environments] == [True, False, True]
    assert len(result.eligible_results) == 2
    assert all(item.validated is not None for item in result.eligible_results)
    assert result.environments[1].failure_phase is not None
    assert result.environments[1].failure_reason is not None


def test_bundle_plan_mismatch_fails_only_that_sibling(tmp_path: Path) -> None:
    early = _environment("EARLY")
    mature = _environment("MATURE")
    bound_root = tmp_path / "bound"
    foreign_root = tmp_path / "foreign"
    bound_root.mkdir()
    foreign_root.mkdir()
    plan, staged = _stage_batch(bound_root, (early, mature))
    foreign_plan = replace(
        _plan((mature,)),
        selection_mode="manual",
        selection_set_code=mature.set_code,
        max_environments=None,
        inventory_payload_digest="b" * 64,
    )
    _, foreign_staged = _stage_batch(foreign_root, (mature,), plan=foreign_plan)

    aggregate = json.loads((staged / "execution.json").read_text())
    bundle_id = aggregate["environments"][1]["bundle_id"]
    bundle = staged / "bundles" / bundle_id / "bundle.json"
    foreign_bundle = foreign_staged / "bundles" / bundle_id / "bundle.json"
    bundle.write_bytes(foreign_bundle.read_bytes())

    result = batch.generate_staged_profile_batch(plan=plan, staged_dir=staged, generated_at=NOW)

    assert hashlib.sha256(foreign_plan.to_bytes()).hexdigest() != hashlib.sha256(plan.to_bytes()).hexdigest()
    assert result.reconciled_counts == {"failed": 1, "planned": 2, "publication_eligible": 1}
    assert {item.environment.set_code: item.publication_eligible for item in result.environments} == {
        "EARLY": True,
        "MATURE": False,
    }
    assert result.environments[1].failure_reason.value == "staged-bundle-load-failed"
    assert result.report.plan_sha256 == hashlib.sha256(plan.to_bytes()).hexdigest()
    report = result.to_json()
    assert report["counts"] == result.reconciled_counts
    assert {item["environment"]["set_code"]: item["outcome"] for item in report["environments"]} == {
        "EARLY": "publication-eligible",
        "MATURE": "failed",
    }


def test_batch_report_excludes_raw_inputs_secrets_and_sensitive_paths(tmp_path: Path) -> None:
    environment = _environment("MATURE")
    plan, staged = _stage_batch(tmp_path, (environment,))

    result = batch.generate_staged_profile_batch(
        plan=plan,
        staged_dir=staged,
        generated_at=NOW,
    )
    serialized = result.to_bytes().decode("utf-8")

    assert str(tmp_path) not in serialized
    assert "inventory.example.test" not in serialized
    assert "lifecycle.example.test" not in serialized
    assert "fixture-secret" not in serialized
    assert "draft_id" not in serialized
    assert "Support Creature" not in serialized
    assert "generated_at" not in serialized
    assert "profile_version" not in serialized
    assert "https://" not in serialized


def test_mixed_maturity_partial_failure_and_repeat_runs_are_deterministic(tmp_path: Path) -> None:
    environments = (_environment("META"), _environment("EARLY"), _environment("MATURE"), _environment("FAIL"))
    plan, staged = _stage_batch(tmp_path, environments, failed_set="FAIL")

    first = batch.generate_staged_profile_batch(plan=plan, staged_dir=staged, generated_at=NOW)
    second = batch.generate_staged_profile_batch(plan=plan, staged_dir=staged, generated_at=NOW)

    assert first.to_bytes() == second.to_bytes()
    assert [item.selection.stage.value for item in first.eligible_results] == ["early", "mature", "metadata"]
    assert first.reconciled_counts == {"failed": 1, "planned": 4, "publication_eligible": 3}
    assert [item.profile_bytes for item in first.eligible_results] == [
        item.profile_bytes for item in second.eligible_results
    ]
    assert [item.failure_reason.value for item in first.environments if not item.publication_eligible] == [
        "refresh-execution-failed"
    ]
    report = first.report.to_json()
    assert report["execution_mode"] in ("online", "offline")
    failed_reports = [item for item in report["environments"] if item["outcome"] == "failed"]
    assert len(failed_reports) == 1
    assert failed_reports[0]["failure_phase"] == "refresh-execution"
    eligible_reports = [item for item in report["environments"] if item["outcome"] == "publication-eligible"]
    assert all(item["sources"] for item in eligible_reports)
    metadata_report = next(
        item for item in eligible_reports if item["environment"]["set_code"] == "META"
    )
    assert any(source["sha256"] for source in metadata_report["sources"])


def test_malformed_execution_authority_is_a_bounded_batch_input_error(tmp_path: Path) -> None:
    environment = _environment("META")
    plan, staged = _stage_batch(tmp_path, (environment,))
    authority_path = staged / "execution.json"
    authority = json.loads(authority_path.read_text())
    authority["plan_sha256"] = "0" * 64
    authority_path.write_text(json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(batch.ProfileBatchGenerationError, match="authority"):
        batch.generate_staged_profile_batch(plan=plan, staged_dir=staged, generated_at=NOW)


@pytest.mark.parametrize("count_name", ("failed", "metadata_only", "planned", "staged"))
def test_tampered_execution_aggregate_count_is_bounded_batch_input_error(
    tmp_path: Path, count_name: str
) -> None:
    environment = _environment("META")
    plan, staged = _stage_batch(tmp_path, (environment,))
    authority_path = staged / "execution.json"
    authority = json.loads(authority_path.read_text())
    authority["counts"][count_name] += 1
    authority_path.write_text(json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(batch.ProfileBatchGenerationError, match="authority"):
        batch.generate_staged_profile_batch(plan=plan, staged_dir=staged, generated_at=NOW)
