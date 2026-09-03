from __future__ import annotations

from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "profile-refresh.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


def test_dispatch_has_only_bounded_manual_and_history_choices() -> None:
    text = _text()
    dispatch = _between(text, "on:\n", "permissions:\n")
    input_names = set(re.findall(r"^      ([a-z_]+):$", dispatch, flags=re.MULTILINE))
    assert input_names == {
        "selection_mode",
        "set_code",
        "event_format",
        "max_environments",
        "lifecycle_url",
        "generated_at",
    }
    choices = _between(dispatch, "        options:\n", "        default: manual")
    assert re.findall(r"^          - (.+)$", choices, flags=re.MULTILINE) == ["manual", "history"]
    assert "active" not in dispatch
    assert "all" not in dispatch
    assert "profile_version" not in dispatch
    assert re.search(
        r"max_environments:\n(?:        [^\n]+\n)+        type: number\n        default: 0",
        dispatch,
    )


def test_manual_zero_sentinel_is_not_a_history_bound() -> None:
    text = _text()
    manual_validation = _between(text, "if [[ \"$SELECTION_MODE\" == manual ]]; then", "else")
    assert 'if [[ "$MAX_ENVIRONMENTS" != 0 ]]; then' in manual_validation
    assert 'dispatch_args+=(--max-environments "$MAX_ENVIRONMENTS")' in manual_validation
    assert 'uv run python scripts/profile_refresh_workflow.py "${dispatch_args[@]}"' in text


def test_validation_helpers_run_before_any_planning_command() -> None:
    text = _text()
    first_plan = text.index("draftomen-tui plan-profile-refresh")
    assert text.index("            validate-dispatch\n") < first_plan
    assert text.index('uv run python scripts/profile_refresh_workflow.py "${dispatch_args[@]}"') < first_plan
    assert text.index("scripts/profile_refresh_workflow.py validate-cache-policy") < first_plan
    for value in ("--freshness-days 7", "--max-entry-bytes 134217728", "--max-total-bytes 536870912", "--max-records 256", "--max-versions-per-source 3"):
        assert value in text

def test_validation_shell_fails_fast_but_later_shells_capture_status() -> None:
    text = _text()
    validation = _between(text, "- name: Validate dispatch and cache policy", "- name: Plan, execute, and generate profile refresh")
    pipeline = _between(text, "- name: Plan, execute, and generate profile refresh", "- name: Render and validate canonical evidence")
    evidence = _between(text, "- name: Render and validate canonical evidence", "- name: Upload canonical profile refresh reports")

    assert re.search(r"^          set -euo pipefail$", validation, flags=re.MULTILINE)
    assert "validate-dispatch" in validation
    assert "validate-cache-policy" in validation
    for later_shell in (pipeline, evidence):
        assert re.search(r"^          set -u$", later_shell, flags=re.MULTILINE)
        assert not re.search(r"^          set -e", later_shell, flags=re.MULTILINE)
    assert "continue-on-error: true" in pipeline
    assert "if: ${{ always() }}" in evidence


def test_planning_selection_and_stage_order_are_explicit() -> None:
    text = _text()
    plan = text.index("draftomen-tui plan-profile-refresh")
    execute = text.index("draftomen-tui execute-profile-refresh")
    batch = text.index("draftomen-tui generate-profile-refresh-batch")
    assert plan < execute < batch
    assert "plan_command+=(--set-code \"$SET_CODE\")" in text
    assert (
        "--history\n"
        "              --max-environments \"$MAX_ENVIRONMENTS\"\n"
        "              --lifecycle-file \"$LIFECYCLE_FILE\""
    ) in text
    assert "fetch-lifecycle" in text
    assert text.index("fetch-lifecycle") < plan
    assert "--cache-dir \"$CACHE_DIR\"" in text
    assert "--output-dir \"$STAGED_DIR\"" in text
    assert "--generated-at \"$GENERATED_AT\"" in text
    assert "--profile-version" not in text
    assert "PROFILE_VERSION" not in text
    assert "--active" not in text
    plan_step = _between(
        text,
        "- name: Plan, execute, and generate profile refresh",
        "- name: Render and validate canonical evidence",
    )
    assert '--lifecycle-url "$LIFECYCLE_URL"' not in plan_step
    assert '--lifecycle-file "$LIFECYCLE_FILE"' in plan_step


def test_runner_temp_cache_and_report_only_upload_are_bounded() -> None:
    text = _text()
    assert "$RUNNER_TEMP/draftomen-profile-refresh-$GITHUB_RUN_ID" in text
    assert "CACHE_DIR=\"$WORK_DIR/profile-input-cache\"" in text
    assert "REPORT_DIR=\"$WORK_DIR/reports\"" in text
    assert "--max-bytes 10485760" in text
    assert "retention-days: 7" in text
    assert "if-no-files-found: error" in text
    upload = text[text.index("uses: actions/upload-artifact@v7") :]
    assert "name: profile-refresh-reports-${{ github.run_id }}-${{ github.run_attempt }}" in upload
    assert "${{ github.run_id }}" in upload
    assert "${{ github.run_attempt }}" in upload
    assert "profile-input-cache" not in upload
    assert "staged" not in upload
    assert "execution.stdout" not in upload
    assert all(name in text for name in ("refresh-plan.json", "execution.json", "batch-report.json", "summary.md"))


def test_canonical_evidence_and_summary_failure_statuses_are_propagated() -> None:
    text = _text()
    evidence = _between(text, "- name: Render and validate canonical evidence", "- name: Upload canonical profile refresh reports")
    assert "render-summary" in evidence
    assert 'cat "$REPORT_DIR/summary.md" >> "$GITHUB_STEP_SUMMARY"' in evidence
    assert "check-report-bundle" in evidence
    assert "stage-status" in evidence
    assert "final-status" in evidence
    assert "plan_status" in evidence
    assert "execution_status" in evidence
    assert "batch_status" in evidence
    assert "bundle_valid" in evidence
    assert 'exit "$final_status"' in text
    assert "continue-on-error: true" in text
    assert "if: ${{ always() }}" in text
    assert "steps.upload-reports.outcome" in text


def test_read_only_and_non_publication_boundaries_are_textually_enforced() -> None:
    text = _text()
    assert text.count("permissions:\n") == 1
    assert "permissions:\n  contents: read\n" in text
    for forbidden in (
        "permissions: write",
        "actions/cache",
        "enable-cache: true",
        "secrets.",
        "GITHUB_TOKEN",
        "token:",
        "password:",
        "publish",
        "release",
        "deploy",
        "environment:",
        "twine",
        "gh release",
    ):
        assert forbidden.lower() not in text.lower()


def test_summary_is_rendered_from_helper_without_dispatch_metadata_or_extra_payloads() -> None:
    text = _text()
    summary = _between(text, "- name: Render and validate canonical evidence", "- name: Upload canonical profile refresh reports")
    assert "render-summary" in summary
    assert "batch-report.json" in summary
    assert 'GENERATED_AT" >> "$GITHUB_STEP_SUMMARY"' not in summary
    assert 'PROFILE_VERSION" >> "$GITHUB_STEP_SUMMARY"' not in summary
    assert "jq " not in summary
    assert "payload" not in summary.lower()
    assert "source_url" not in summary
    assert "diagnostic" not in summary.lower()
