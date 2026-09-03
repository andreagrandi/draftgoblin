from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

import scripts.profile_refresh_workflow as workflow
from draftomen.profile_refresh_execution import DEFAULT_PROFILE_REFRESH_CACHE_POLICY
from scripts.profile_refresh_workflow import (
    EXPECTED_REPORT_FILES,
    MAX_LIFECYCLE_RESPONSE_BYTES,
    MAX_REPORT_BUNDLE_BYTES,
    ProfileRefreshWorkflowError,
    fetch_lifecycle_document,
    render_summary,
    validate_cache_policy,
    validate_dispatch_inputs,
    validate_report_bundle,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _dispatch(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "selection_mode": "manual",
        "set_code": "TST",
        "max_environments": None,
        "lifecycle_url": None,
        "event_format": "quickdraft",
        "generated_at": NOW,
    }
    values.update(overrides)
    return values


def test_manual_dispatch_is_valid() -> None:
    assert validate_dispatch_inputs(**_dispatch()) is None


def test_history_dispatch_is_valid_with_public_lifecycle_url() -> None:
    assert validate_dispatch_inputs(
        "history", None, "2", "https://schedule.example.test/lifecycle.json", "quickdraft", NOW
    ) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"selection_mode": "active"},
        {"selection_mode": "all"},
        {"set_code": ""},
        {"set_code": None},
        {"max_environments": 1},
        {"generated_at": datetime(2026, 9, 1, 12, 0)},
        {"event_format": ""},
    ],
)
def test_manual_dispatch_rejects_implicit_or_ambiguous_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_dispatch_inputs(**_dispatch(**overrides))


@pytest.mark.parametrize(
    "max_environments",
    [None, "", "0", 0, "-1", -1, "1.5", 1.5, True],
)
def test_history_requires_positive_integer_bound(max_environments: object) -> None:
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_dispatch_inputs(
            "history", None, max_environments, "https://schedule.example.test/lifecycle.json", "quickdraft", NOW
        )


@pytest.mark.parametrize("set_code", ["TST", ""])
def test_history_rejects_set_code(set_code: str) -> None:
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_dispatch_inputs(
            "history", set_code, 1, "https://schedule.example.test/lifecycle.json", "quickdraft", NOW
        )


@pytest.mark.parametrize("lifecycle_url", [None, "", "lifecycle.json", "http://schedule.example.test/lifecycle.json", "file:///tmp/lifecycle.json"])
def test_history_requires_public_https_lifecycle_url(lifecycle_url: str | None) -> None:
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_dispatch_inputs("history", None, 1, lifecycle_url, "quickdraft", NOW)


@pytest.mark.parametrize(
    "hostname",
    [
        "-bad.example.test",
        "bad-.example.test",
        "bad_name.example.test",
        "bad..example.test",
        f"{'a' * 64}.example.test",
    ],
)
def test_history_rejects_malformed_dns_hostname(hostname: str) -> None:
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_dispatch_inputs(
            "history", None, 1, f"https://{hostname}/lifecycle.json", "quickdraft", NOW
        )


def test_history_accepts_valid_dns_hostname() -> None:
    assert validate_dispatch_inputs(
        "history", None, 1, "https://valid-host.example.test/lifecycle.json", "quickdraft", NOW
    ) is None


def test_history_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_dispatch_inputs(
            "history", None, 1, "https://schedule.example.test/lifecycle.json", "quickdraft", "2026-09-01T12:00:00"
        )


class _FakeLifecycleResponse:
    def __init__(self, status: int, body: bytes = b"", location: str | None = None) -> None:
        self.status = status
        self._body = body
        self._location = location

    def getheader(self, name: str) -> str | None:
        if name == "Location":
            return self._location
        if name == "Content-Length":
            return str(len(self._body))
        return None

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._body)
        body, self._body = self._body[:amount], self._body[amount:]
        return body

    def close(self) -> None:
        pass


class _FakeLifecycleConnection:
    def close(self) -> None:
        pass


def test_pinned_connection_uses_validated_address_and_original_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class _FakeSocket:
        def __init__(self, family: int, socket_type: int) -> None:
            calls["socket"] = self
            calls["family"] = family
            calls["socket_type"] = socket_type

        def settimeout(self, timeout: float | None) -> None:
            calls["timeout"] = timeout

        def connect(self, destination: object) -> None:
            calls["destination"] = destination

        def close(self) -> None:
            calls["closed"] = True

    wrapped_socket = object()

    class _FakeTLSContext:
        def wrap_socket(self, sock: object, *, server_hostname: str) -> object:
            calls["wrapped_socket"] = sock
            calls["server_hostname"] = server_hostname
            return wrapped_socket

    def fail_dns_lookup(*args: object, **kwargs: object) -> object:
        pytest.fail("pinned connection must not perform a DNS lookup")

    monkeypatch.setattr(workflow.socket, "getaddrinfo", fail_dns_lookup)
    monkeypatch.setattr(workflow.socket, "socket", _FakeSocket)
    connection = workflow._PinnedHTTPSConnection(
        host="schedule.example",
        port=443,
        validated_address="93.184.216.34",
        timeout=7,
        context=_FakeTLSContext(),
    )

    connection.connect()

    assert connection.host == "schedule.example"
    assert connection.timeout == 7
    assert connection._validated_address == "93.184.216.34"
    assert calls["family"] == workflow.socket.AF_INET
    assert calls["socket_type"] == workflow.socket.SOCK_STREAM
    assert calls["timeout"] == 7
    assert calls["destination"] == ("93.184.216.34", 443)
    assert calls["wrapped_socket"] is calls["socket"]
    assert calls["server_hostname"] == "schedule.example"
    assert connection.sock is wrapped_socket


def test_lifecycle_fetch_rejects_malformed_and_non_global_literals(tmp_path: Path) -> None:
    for url in ("https://[malformed", "https://192.0.2.1/lifecycle.json"):
        with pytest.raises(ProfileRefreshWorkflowError):
            fetch_lifecycle_document(url, tmp_path / "lifecycle.json")


def test_lifecycle_fetch_rejects_private_dns_without_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        workflow.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (workflow.socket.AF_INET, workflow.socket.SOCK_STREAM, 0, "", ("10.0.0.1", 443))
        ],
    )
    with pytest.raises(ProfileRefreshWorkflowError):
        fetch_lifecycle_document("https://schedule.example/lifecycle.json", tmp_path / "lifecycle.json")


@pytest.mark.parametrize(
    "location",
    ["http://public.example/lifecycle.json", "https://127.0.0.1/lifecycle.json"],
)
def test_lifecycle_fetch_rejects_downgrade_or_private_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    location: str,
) -> None:
    monkeypatch.setattr(workflow, "_resolve_global_addresses", lambda host, port: ("93.184.216.34",))
    calls: list[str] = []

    def request(parsed: object, address: str, *, timeout_seconds: int) -> tuple[object, object]:
        calls.append(parsed.geturl())
        return _FakeLifecycleConnection(), _FakeLifecycleResponse(302, location=location)

    monkeypatch.setattr(workflow, "_request_lifecycle_hop", request)
    with pytest.raises(ProfileRefreshWorkflowError):
        fetch_lifecycle_document("https://public.example/lifecycle.json", tmp_path / "lifecycle.json")
    assert calls == ["https://public.example/lifecycle.json"]


def test_lifecycle_fetch_validates_and_pins_each_public_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    addresses = {
        "public.example": "93.184.216.34",
        "next.example": "93.184.216.35",
    }
    resolved: list[str] = []
    pinned: list[tuple[str, str]] = []

    def resolve(host: str, port: int) -> tuple[str, ...]:
        resolved.append(host)
        return (addresses[host],)

    responses = iter(
        (
            _FakeLifecycleResponse(302, location="https://next.example/lifecycle.json"),
            _FakeLifecycleResponse(200, body=b'{"provider":"schedule","historical":["TST"]}'),
        )
    )

    def request(parsed: object, address: str, *, timeout_seconds: int) -> tuple[object, object]:
        pinned.append((parsed.hostname, address))
        return _FakeLifecycleConnection(), next(responses)

    monkeypatch.setattr(workflow, "_resolve_global_addresses", resolve)
    monkeypatch.setattr(workflow, "_request_lifecycle_hop", request)
    output = tmp_path / "nested" / "lifecycle.json"
    fetch_lifecycle_document("https://public.example/lifecycle.json", output)
    assert resolved == ["public.example", "next.example"]
    assert pinned == [
        ("public.example", "93.184.216.34"),
        ("next.example", "93.184.216.35"),
    ]
    assert json.loads(output.read_text(encoding="utf-8"))["source_url"] == (
        "https://next.example/lifecycle.json"
    )


def test_fetch_lifecycle_subcommand_writes_runner_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(workflow, "_resolve_global_addresses", lambda host, port: ("93.184.216.34",))
    response = _FakeLifecycleResponse(200, body=b'{"historical":["TST"]}')
    monkeypatch.setattr(
        workflow,
        "_request_lifecycle_hop",
        lambda parsed, address, *, timeout_seconds: (_FakeLifecycleConnection(), response),
    )
    output = tmp_path / "lifecycle.json"
    assert workflow.main(
        [
            "fetch-lifecycle",
            "--lifecycle-url",
            "https://public.example/lifecycle.json",
            "--output",
            str(output),
        ]
    ) == 0
    assert output.exists()


def test_lifecycle_fetch_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(workflow, "_resolve_global_addresses", lambda host, port: ("93.184.216.34",))
    response = _FakeLifecycleResponse(200, body=b"x" * (MAX_LIFECYCLE_RESPONSE_BYTES + 1))
    monkeypatch.setattr(
        workflow,
        "_request_lifecycle_hop",
        lambda parsed, address, *, timeout_seconds: (_FakeLifecycleConnection(), response),
    )
    with pytest.raises(ProfileRefreshWorkflowError):
        fetch_lifecycle_document("https://public.example/lifecycle.json", tmp_path / "lifecycle.json")


def _policy_values() -> dict[str, object]:
    return {
        "freshness_days": DEFAULT_PROFILE_REFRESH_CACHE_POLICY.freshness_ttl.days,
        "max_entry_bytes": DEFAULT_PROFILE_REFRESH_CACHE_POLICY.max_entry_bytes,
        "max_total_bytes": DEFAULT_PROFILE_REFRESH_CACHE_POLICY.max_total_bytes,
        "max_records": DEFAULT_PROFILE_REFRESH_CACHE_POLICY.max_records,
        "max_versions_per_source": DEFAULT_PROFILE_REFRESH_CACHE_POLICY.max_versions_per_source,
    }


def test_cache_policy_matches_repository_defaults() -> None:
    assert validate_cache_policy(**_policy_values()) is None


@pytest.mark.parametrize("field", ["freshness_days", "max_entry_bytes", "max_total_bytes", "max_records", "max_versions_per_source"])
def test_cache_policy_rejects_drift(field: str) -> None:
    values = _policy_values()
    values[field] = int(values[field]) + 1
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_cache_policy(**values)


def _write_expected_bundle(root: Path, *, total_bytes: int = 0) -> None:
    root.mkdir()
    names = sorted(EXPECTED_REPORT_FILES)
    for index, name in enumerate(names):
        size = total_bytes if index == 0 else 0
        (root / name).write_bytes(b"x" * size)


def test_report_bundle_accepts_exact_files_at_size_limit(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    _write_expected_bundle(report_dir, total_bytes=MAX_REPORT_BUNDLE_BYTES)
    assert validate_report_bundle(report_dir) == MAX_REPORT_BUNDLE_BYTES


def test_report_bundle_rejects_one_byte_overflow(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    _write_expected_bundle(report_dir, total_bytes=MAX_REPORT_BUNDLE_BYTES + 1)
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_report_bundle(report_dir)


def test_report_bundle_rejects_missing_extra_and_staged_files(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    _write_expected_bundle(report_dir)
    (report_dir / "summary.md").unlink()
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_report_bundle(report_dir)

    (report_dir / "summary.md").write_text("summary", encoding="utf-8")
    (report_dir / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ProfileRefreshWorkflowError):
        validate_report_bundle(report_dir)


def _report() -> dict[str, object]:
    digest = "a" * 64
    return {
        "schema_version": 1,
        "selection_mode": "history",
        "generated_at": "SECRET-TIMESTAMP",
        "profile_version": "SECRET-PROFILE-VERSION",
        "versions": {"generator_version": "generator-1", "statistics_version": 3, "hostile": "ignore"},
        "counts": {"planned": 2, "publication_eligible": 1, "failed": 1, "hostile": "ignore"},
        "environments": [
            {
                "environment": {"set_code": "TST_[one]", "event_format": "Quick*Draft", "lifecycle": "historical", "source_url": "https://secret.example"},
                "outcome": "publication-eligible",
                "selection": {"stage": "mature", "diagnostics": "do not show"},
                "sources": [
                    {
                        "name": "ratings_[source]",
                        "sha256": digest,
                        "attribution": "Attribution (safe)",
                        "license": "CC BY 4.0",
                        "url": "https://secret.example/raw.csv",
                        "path": "/tmp/raw.csv",
                        "payload": "SECRET DATA",
                    }
                ],
                "samples": {"total": 12, "by_pair": {"WU": 7, "UB": 5, "card_name": 99}},
                "card_games": 20,
                "pair_games": 10,
                "skip_count": 3,
                "skip_reasons": {
                    "missing_pick": 2,
                    "outside_set": 1,
                    "hostile": {"secret": "TOP-SECRET"},
                    "https://attacker.example": {"card_name": "Black Lotus"},
                    "/tmp/raw": 99,
                },
                "error_count": 1,
                "error_reasons": {
                    "format_error": 1,
                    "hostile": {"secret": "TOP-SECRET"},
                    "https://attacker.example/error": {"path": "/tmp/error"},
                    "/tmp/error": 7,
                },
                "failure_reason": "SECRET EXCEPTION",
                "diagnostics": ["SECRET DIAGNOSTICS"],
                "artifacts": {
                    "profile": {"sha256": digest, "bytes": 100},
                    "gzip": {"sha256": "b" * 64, "bytes": 50},
                    "report": {"sha256": "c" * 64, "bytes": 75},
                },
            },
            {
                "environment": {"set_code": "FAIL", "event_format": "quickdraft", "lifecycle": None},
                "outcome": "failed",
                "selection": None,
                "samples": None,
                "card_games": None,
                "pair_games": None,
                "skip_count": None,
                "skip_reasons": {"card-database-unavailable": 1},
                "error_count": None,
                "error_reasons": {},
                "failure_phase": "refresh-execution",
                "failure_reason": "refresh-execution-failed",
                "artifacts": {
                    "profile": {"sha256": None, "bytes": None},
                    "gzip": {"sha256": None, "bytes": None},
                    "report": {"sha256": None, "bytes": None},
                },
                "raw_payload": "do not show this payload",
            },
        ],
    }


def test_summary_is_complete_deterministic_and_allowlisted() -> None:
    report = _report()
    first = render_summary(report)
    second = render_summary(json.loads(json.dumps(report)))
    assert first == second
    failed_section = first[first.index("### Environment 2") :]
    for expected in (
        "- Sample total: not reported",
        "- Samples by pair: not reported",
        "- Card games: not reported",
        "- Pair games: not reported",
        "- Skips: not reported",
        "- Skip reasons: card\\-database\\-unavailable=1",
        "- Errors: not reported",
    ):
        assert expected in failed_section
    for expected in (
        "TST\\_\\[one\\]",
        "Quick\\*Draft",
        "historical",
        "publication\\-eligible",
        "Generation stage: mature",
        "Schema version: 1",
        "Generator version: generator\\-1",
        "Statistics version: 3",
        "Planned: 2",
        "Publication-eligible: 1",
        "Failed: 1",
        "Sample total: 12",
        "Samples by pair: WU=7, UB=5",
        "Card games: 20",
        "Pair games: 10",
        "Skip reasons: missing\\_pick=2, outside\\_set=1",
        "Errors: 1",
        "Error reasons: format\\_error=1",
        "Name: ratings\\_\\[source\\]",
        "Digest: " + "a" * 64,
        "Attribution: Attribution \\(safe\\)",
        "License: CC BY 4\\.0",
        "SHA-256 " + "a" * 64,
        "bytes 100",
        "FAIL",
        "Outcome: failed",
        "Sample total: not reported",
        "Samples by pair: not reported",
        "Card games: not reported",
        "Pair games: not reported",
        "Skips: not reported",
        "Skip reasons: card\\-database\\-unavailable=1",
        "Errors: not reported",
    ):
        assert expected in first
    for forbidden in (
        "SECRET-TIMESTAMP",
        "SECRET-PROFILE-VERSION",
        "https://secret.example",
        "/tmp/raw.csv",
        "SECRET DATA",
        "SECRET EXCEPTION",
        "SECRET DIAGNOSTICS",
        "do not show this payload",
        "card_name",
        "99",
        "TOP-SECRET",
        "attacker.example",
        "Black Lotus",
        "/tmp/error",
    ):
        assert forbidden not in first


def test_summary_escapes_html_in_allowed_source_metadata() -> None:
    report = _report()
    source = report["environments"][0]["sources"][0]
    source["name"] = "<img src=x onerror=alert(1)> & [source]"
    source["attribution"] = "<b>Trusted & credited"
    source["license"] = "CC <script>alert(1)"

    summary = render_summary(report)

    assert "<" not in summary
    assert ">" not in summary
    assert "Name: &lt;img src=x onerror=alert\\(1\\)&gt; &amp; \\[source\\]" in summary
    assert "Attribution: &lt;b&gt;Trusted &amp; credited" in summary
    assert "License: CC &lt;script&gt;alert\\(1\\)" in summary
    assert "&amp;lt;" not in summary
    assert "&amp;gt;" not in summary


def test_summary_ignores_hostile_extra_keys() -> None:
    report = _report()
    report["secret"] = {"token": "TOP-SECRET", "payload": "raw bytes"}
    assert "TOP-SECRET" not in render_summary(report)
