"""Validation and report-rendering helpers for the profile refresh workflow.

This module intentionally contains no workflow orchestration.  It gives the
workflow small, testable checks for its operator-facing inputs and evidence.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
from typing import Any, TypeAlias
from urllib.parse import urljoin, urlsplit, urlunsplit

from draftomen.profile_refresh_execution import DEFAULT_PROFILE_REFRESH_CACHE_POLICY
from draftomen.config import COLOR_PAIRS


PathInput: TypeAlias = str | os.PathLike[str]


EXPECTED_REPORT_FILES = frozenset(
    {"refresh-plan.json", "execution.json", "batch-report.json", "summary.md"}
)
MAX_REPORT_BUNDLE_BYTES = 10 * 1024 * 1024
MAX_LIFECYCLE_REDIRECTS = 5
MAX_LIFECYCLE_RESPONSE_BYTES = 1 * 1024 * 1024
LIFECYCLE_FETCH_TIMEOUT_SECONDS = 10
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_INTEGER_PATTERN = re.compile(r"[0-9]+\Z")
_DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_UNSAFE_TEXT_PATTERN = re.compile(r"[\x00-\x1f\x7f]|[\\/]|://")

# These values are the finite vocabularies emitted by the canonical profile
# input, public-dump, generation, and execution producers.  Summary rendering
# iterates these sets rather than trusting report-controlled mapping keys.
_APPROVED_SKIP_REASONS = frozenset(
    {
        "blank_row",
        "below_maindeck_threshold",
        "bundle-environment-mismatch",
        "card-database-required",
        "card-database-unavailable",
        "card-metadata-cache-corrupt",
        "card-metadata-cache-missing",
        "card-metadata-cache-store-failed",
        "card-metadata-cache-unavailable",
        "card-metadata-refresh-failed",
        "card-metadata-unavailable",
        "extra_fields",
        "deck_inferred_lands_out_of_range",
        "deck_unresolved_two_color_pair",
        "card_rating_invalid_sample_count",
        "card_rating_missing_rate",
        "card_rating_out_of_range",
        "card_rating_out_of_set",
        "card_rating_unmatched_metadata",
        "duplicate_card_rating_key",
        "draft_source_not_selected",
        "malformed_normalized_row",
        "missing_draft_id",
        "missing_fields",
        "missing_maindeck_rate",
        "missing_pick",
        "not_trophy_deck",
        "unknown_card",
        "outside_format",
        "outside_set",
        "pair_performance_malformed",
        "role_profile_compile_failed",
        "remote_source_not_read",
        "required-input-staging-failed",
        "role_classification_failed",
        "17lands-public-drafts-cache-corrupt",
        "17lands-public-drafts-cache-missing",
        "17lands-public-drafts-cache-store-failed",
        "17lands-public-drafts-cache-unavailable",
        "17lands-public-drafts-refresh-failed",
        "17lands-public-drafts-staging-failed",
        "17lands-public-drafts-unavailable",
        "17lands-ratings-cache-corrupt",
        "17lands-ratings-cache-missing",
        "17lands-ratings-cache-store-failed",
        "17lands-ratings-cache-unavailable",
        "17lands-ratings-refresh-failed",
        "17lands-ratings-staging-failed",
        "17lands-ratings-unavailable",
    }
)
_APPROVED_ERROR_REASONS = frozenset(
    {
        "format_error",
        "malformed_csv",
        "public_dump_error",
        "read_error",
        "structure_target_error",
    }
)

class ProfileRefreshWorkflowError(ValueError):
    """Raised when workflow inputs or evidence are not safe to use."""


def validate_dispatch_inputs(
    selection_mode: str,
    set_code: str | None,
    max_environments: str | int | None,
    lifecycle_url: str | None,
    event_format: str | None,
    generated_at: str | datetime | None,
) -> None:
    """Validate the explicit, bounded inputs accepted by the workflow.

    ``max_environments`` accepts a string because the workflow receives its
    dispatch values from the shell.  It is deliberately parsed here rather
    than relying on shell arithmetic, which would accept ambiguous values.
    """

    if not isinstance(selection_mode, str) or selection_mode not in {"manual", "history"}:
        raise ProfileRefreshWorkflowError("selection mode is invalid")
    if not isinstance(event_format, str) or not event_format.strip():
        raise ProfileRefreshWorkflowError("event format is required")
    _parse_timezone_aware_timestamp(generated_at)

    if selection_mode == "manual":
        if not isinstance(set_code, str) or not set_code.strip():
            raise ProfileRefreshWorkflowError("manual selection requires a set code")
        if max_environments is not None:
            raise ProfileRefreshWorkflowError("manual selection cannot have a history bound")
        return

    if set_code is not None:
        raise ProfileRefreshWorkflowError("history selection cannot have a set code")
    _validate_public_url(lifecycle_url)
    bound = _parse_integer(max_environments)
    if bound < 1:
        raise ProfileRefreshWorkflowError("history bound must be positive")


def validate_cache_policy(
    *,
    freshness_days: str | int = 7,
    max_entry_bytes: str | int = 128 * 1024 * 1024,
    max_total_bytes: str | int = 512 * 1024 * 1024,
    max_records: str | int = 256,
    max_versions_per_source: str | int = 3,
) -> None:
    """Reject workflow cache declarations that drift from the repository policy."""

    declared = {
        "freshness_days": _parse_integer(freshness_days),
        "max_entry_bytes": _parse_integer(max_entry_bytes),
        "max_total_bytes": _parse_integer(max_total_bytes),
        "max_records": _parse_integer(max_records),
        "max_versions_per_source": _parse_integer(max_versions_per_source),
    }
    policy = DEFAULT_PROFILE_REFRESH_CACHE_POLICY
    if (
        policy.freshness_ttl != timedelta(days=declared["freshness_days"])
        or declared["max_entry_bytes"] != policy.max_entry_bytes
        or declared["max_total_bytes"] != policy.max_total_bytes
        or declared["max_records"] != policy.max_records
        or declared["max_versions_per_source"] != policy.max_versions_per_source
    ):
        raise ProfileRefreshWorkflowError("declared cache policy differs from repository policy")


def validate_report_bundle(
    report_dir: PathInput,
    *,
    max_bytes: int = MAX_REPORT_BUNDLE_BYTES,
) -> int:
    """Validate the exact report bundle and return its aggregate byte size.

    Only the four path-free report files are accepted.  In particular, a
    directory, symlink, staged bundle, or cache entry cannot be hidden inside
    the upload directory.
    """

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ProfileRefreshWorkflowError("report bundle size limit is invalid")
    try:
        root = Path(os.fspath(report_dir))
        if not root.is_dir() or root.is_symlink():
            raise ProfileRefreshWorkflowError("report bundle directory is invalid")
        entries = tuple(root.iterdir())
    except (OSError, TypeError, ValueError) as error:
        raise ProfileRefreshWorkflowError("report bundle directory is invalid") from error

    names = {entry.name for entry in entries}
    if names != EXPECTED_REPORT_FILES or any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise ProfileRefreshWorkflowError("report bundle files are invalid")

    total = 0
    try:
        for name in sorted(EXPECTED_REPORT_FILES):
            size = (root / name).stat().st_size
            if size < 0:
                raise ProfileRefreshWorkflowError("report bundle file size is invalid")
            total += size
    except (OSError, ValueError) as error:
        raise ProfileRefreshWorkflowError("report bundle file size is invalid") from error
    if total > max_bytes:
        raise ProfileRefreshWorkflowError("report bundle exceeds size limit")
    return total


def render_summary(report: Mapping[str, Any]) -> str:
    """Render a deterministic Markdown summary from canonical batch-report fields.

    The renderer intentionally reads only the fields listed below.  Unknown
    fields are ignored, including fields that could contain raw payloads,
    paths, URLs, diagnostics, or secrets.
    """

    if not isinstance(report, Mapping):
        raise ProfileRefreshWorkflowError("batch report is invalid")

    versions = _mapping(report.get("versions"))
    schema_version = report.get("schema_version")
    generator_version = versions.get("generator_version")
    profile_generation_schema_version = versions.get("profile_generation_schema_version")
    profile_generation_execution_schema_version = versions.get("profile_generation_execution_schema_version")
    set_profile_schema_version = versions.get("set_profile_schema_version")
    public_dump_manifest_schema_version = versions.get("public_dump_manifest_schema_version")
    statistics_version = versions.get("statistics_version")
    counts = _mapping(report.get("counts"))
    planned_count = counts.get("planned")
    eligible_count = counts.get("publication_eligible")
    failed_count = counts.get("failed")
    lines = [
        "# Profile refresh batch report",
        "",
        "## Batch versions",
        f"- Schema version: {_version_number(schema_version)}",
        f"- Generator version: {_version_text(generator_version)}",
        f"- Profile generation schema version: {_version_number(profile_generation_schema_version)}",
        f"- Profile generation execution schema version: {_version_number(profile_generation_execution_schema_version)}",
        f"- Set profile schema version: {_version_number(set_profile_schema_version)}",
        f"- Public dump manifest schema version: {_version_number(public_dump_manifest_schema_version)}",
        f"- Statistics version: {_version_number(statistics_version)}",
        "",
        "## Run counts",
        f"- Planned: {_count_value(planned_count)}",
        f"- Publication-eligible: {_count_value(eligible_count)}",
        f"- Failed: {_count_value(failed_count)}",
        "",
        "## Environments",
    ]

    environments = report.get("environments")
    if isinstance(environments, Sequence) and not isinstance(environments, (str, bytes, bytearray)):
        for index, value in enumerate(environments, start=1):
            lines.extend(_render_environment(value, index))
    else:
        lines.append("- No environment results reported.")
    return "\n".join(lines) + "\n"


def _render_environment(value: Any, index: int) -> list[str]:
    environment = _mapping(value)
    identity = _mapping(environment.get("environment"))
    set_code = _safe_text(identity.get("set_code"))
    event_format = _safe_text(identity.get("event_format"))
    lifecycle_value = identity.get("lifecycle")
    lifecycle = (
        _safe_text(lifecycle_value)
        if isinstance(lifecycle_value, str) and lifecycle_value in {"active", "mature", "historical"}
        else ""
    )
    outcome_value = environment.get("outcome")
    outcome = (
        _safe_text(outcome_value)
        if isinstance(outcome_value, str) and outcome_value in {"publication-eligible", "failed"}
        else ""
    )
    not_reported = "not reported"
    lines = [
        "",
        f"### Environment {index}: {set_code or not_reported}",
        f"- Set code: {set_code or not_reported}",
        f"- Event format: {event_format or not_reported}",
        f"- Lifecycle: {lifecycle or not_reported}",
        f"- Outcome: {outcome or not_reported}",
    ]

    selection = _mapping(environment.get("selection"))
    stage = selection.get("stage")
    if isinstance(stage, str) and stage in {"metadata", "early", "mature"}:
        lines.append(f"- Generation stage: {_escape_markdown(stage)}")

    samples = _mapping(environment.get("samples"))
    sample_total = samples.get("total")
    lines.append(f"- Sample total: {_count_value(sample_total)}")
    pairs = _pair_counts(samples.get("by_pair"))
    rendered_pairs = ", ".join(
        f"{_escape_markdown(pair)}={count}" for pair, count in pairs
    )
    lines.append(f"- Samples by pair: {rendered_pairs or not_reported}")

    for field_name, label in (("card_games", "Card games"), ("pair_games", "Pair games")):
        lines.append(f"- {label}: {_count_value(environment.get(field_name))}")

    _append_count_and_reasons(lines, environment, "skip_count", "skip_reasons", "Skips", "Skip reasons")
    _append_count_and_reasons(lines, environment, "error_count", "error_reasons", "Errors", "Error reasons")
    _append_sources(lines, environment.get("sources"))
    _append_artifacts(lines, environment.get("artifacts"))
    return lines


def _append_count_and_reasons(
    lines: list[str],
    environment: Mapping[str, Any],
    count_key: str,
    reasons_key: str,
    count_label: str,
    reasons_label: str,
) -> None:
    count = environment.get(count_key)
    if reasons_key == "skip_reasons":
        allowed = _APPROVED_SKIP_REASONS
    elif reasons_key == "error_reasons":
        allowed = _APPROVED_ERROR_REASONS
    else:
        allowed = frozenset()
    reasons = _reason_counts(environment.get(reasons_key), allowed=allowed)
    lines.append(f"- {count_label}: {_count_value(count)}")
    if reasons:
        rendered = ", ".join(f"{reason}={number}" for reason, number in reasons)
        lines.append(f"- {reasons_label}: {rendered}")


def _append_sources(lines: list[str], value: Any) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return
    sources: list[tuple[str, str, str, str]] = []
    for candidate in value:
        source = _mapping(candidate)
        name = _safe_text(source.get("name"))
        digest = source.get("sha256")
        if not name or not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            continue
        attribution = _safe_text(source.get("attribution"))
        license_name = _safe_text(source.get("license"))
        sources.append((name, digest, attribution, license_name))
    if not sources:
        return
    lines.append("- Sources:")
    for name, digest, attribution, license_name in sorted(sources):
        lines.append(f"  - Name: {name}")
        lines.append(f"    Digest: {_escape_markdown(digest)}")
        if attribution:
            lines.append(f"    Attribution: {attribution}")
        if license_name:
            lines.append(f"    License: {license_name}")


def _append_artifacts(lines: list[str], value: Any) -> None:
    artifacts = _mapping(value)
    rendered: list[str] = []
    for key, label in (("profile", "Profile"), ("gzip", "Gzip"), ("report", "Generation report")):
        artifact = _mapping(artifacts.get(key))
        digest = artifact.get("sha256")
        size = artifact.get("bytes")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None or not _is_count(size):
            continue
        rendered.append(f"  - {label}: SHA-256 {_escape_markdown(digest)}; bytes {size}")
    if rendered:
        lines.append("- Artifacts:")
        lines.extend(rendered)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _count_value(value: Any) -> str:
    return str(value) if _is_count(value) else "not reported"


def _pair_counts(value: Any) -> tuple[tuple[str, int], ...]:
    mapping = _mapping(value)
    result: list[tuple[str, int]] = []
    for pair in COLOR_PAIRS:
        count = mapping.get(pair)
        if _is_count(count):
            result.append((pair, count))
    return tuple(result)

def _reason_counts(
    value: Any,
    *,
    allowed: frozenset[str],
) -> tuple[tuple[str, int], ...]:
    mapping = _mapping(value)
    return tuple(
        (_escape_markdown(reason), mapping[reason])
        for reason in sorted(allowed)
        if _is_count(mapping.get(reason))
    )


def _safe_text(value: Any) -> str:
    if not isinstance(value, str) or not value or _UNSAFE_TEXT_PATTERN.search(value):
        return ""
    return _escape_markdown(value)


def _version_number(value: Any) -> str:
    return str(value) if _is_count(value) else "not reported"


def _version_text(value: Any) -> str:
    safe = _safe_text(value)
    return safe or "not reported"


def _escape_markdown(value: str) -> str:
    text = "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in value)
    # Escape HTML before Markdown so the entities introduced here are not escaped again.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~])", r"\\\1", text)


def _parse_integer(value: str | int | None) -> int:
    if isinstance(value, bool):
        raise ProfileRefreshWorkflowError("integer value is invalid")
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or _INTEGER_PATTERN.fullmatch(value.strip()) is None:
        raise ProfileRefreshWorkflowError("integer value is invalid")
    return int(value.strip())


def _parse_timezone_aware_timestamp(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str) and value.strip():
        try:
            timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise ProfileRefreshWorkflowError("generated timestamp is invalid") from error
    else:
        raise ProfileRefreshWorkflowError("generated timestamp is invalid")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ProfileRefreshWorkflowError("generated timestamp must be timezone-aware")
    return timestamp.astimezone(UTC)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose socket is pinned to an already checked address."""

    def __init__(
        self,
        host: str,
        port: int,
        validated_address: str,
        timeout: float | None = None,
        *,
        context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(host=host, port=port, timeout=timeout, context=context)
        self._validated_address = validated_address

    def connect(self) -> None:
        try:
            address = ipaddress.ip_address(self._validated_address)
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sockaddr = (
                (self._validated_address, self.port, 0, 0)
                if family == socket.AF_INET6
                else (self._validated_address, self.port)
            )
            sock.connect(sockaddr)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            if "sock" in locals():
                sock.close()
            raise


def _resolve_global_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ProfileRefreshWorkflowError("lifecycle host cannot be resolved") from error

    addresses: list[str] = []
    for info in infos:
        try:
            family, _, _, _, sockaddr = info
            address_text = sockaddr[0]
            address = ipaddress.ip_address(address_text)
        except (IndexError, TypeError, ValueError):
            raise ProfileRefreshWorkflowError("lifecycle host address is invalid") from None
        if family not in {socket.AF_INET, socket.AF_INET6} or not address.is_global:
            raise ProfileRefreshWorkflowError("lifecycle host address is not public")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise ProfileRefreshWorkflowError("lifecycle host has no public address")
    return tuple(addresses)


def _request_lifecycle_hop(
    parsed: Any,
    address: str,
    *,
    timeout_seconds: int,
) -> tuple[_PinnedHTTPSConnection, http.client.HTTPResponse]:
    host = parsed.hostname
    port = parsed.port or 443
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection = _PinnedHTTPSConnection(
        host=host,
        port=port,
        validated_address=address,
        timeout=timeout_seconds,
    )
    try:
        connection.request(
            "GET",
            target,
            headers={"User-Agent": "draftomen-profile-refresh"},
        )
        return connection, connection.getresponse()
    except Exception:
        connection.close()
        raise


def _read_lifecycle_response(response: Any) -> bytes:
    content_length = response.getheader("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            raise ProfileRefreshWorkflowError("lifecycle response size is invalid") from None
        if declared < 0 or declared > MAX_LIFECYCLE_RESPONSE_BYTES:
            raise ProfileRefreshWorkflowError("lifecycle response exceeds size limit")

    body = bytearray()
    while True:
        chunk = response.read(min(64 * 1024, MAX_LIFECYCLE_RESPONSE_BYTES + 1 - len(body)))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise ProfileRefreshWorkflowError("lifecycle response is invalid")
        body.extend(chunk)
        if len(body) > MAX_LIFECYCLE_RESPONSE_BYTES:
            raise ProfileRefreshWorkflowError("lifecycle response exceeds size limit")
    return bytes(body)


def _fetch_lifecycle_payload(
    url: str,
    *,
    timeout_seconds: int = LIFECYCLE_FETCH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise ProfileRefreshWorkflowError("lifecycle timeout is invalid")
    current_url = url.strip() if isinstance(url, str) else ""
    for redirect_count in range(MAX_LIFECYCLE_REDIRECTS + 1):
        _validate_public_url(current_url)
        parsed = urlsplit(current_url)
        addresses = _resolve_global_addresses(parsed.hostname, parsed.port or 443)
        try:
            connection, response = _request_lifecycle_hop(
                parsed,
                addresses[0],
                timeout_seconds=timeout_seconds,
            )
        except ProfileRefreshWorkflowError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ProfileRefreshWorkflowError("lifecycle request failed") from error
        try:
            status = response.status
            if status in {301, 302, 303, 307, 308}:
                if redirect_count >= MAX_LIFECYCLE_REDIRECTS:
                    raise ProfileRefreshWorkflowError("lifecycle redirects exceed limit")
                location = response.getheader("Location")
                if not isinstance(location, str) or not location.strip():
                    raise ProfileRefreshWorkflowError("lifecycle redirect is invalid")
                current_url = urljoin(current_url, location.strip())
                continue
            if status != 200:
                raise ProfileRefreshWorkflowError("lifecycle response status is invalid")
            body = _read_lifecycle_response(response)
        except ProfileRefreshWorkflowError:
            raise
        except (AttributeError, OSError, TypeError, ValueError) as error:
            raise ProfileRefreshWorkflowError("lifecycle response is invalid") from error
        finally:
            response.close()
            connection.close()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise ProfileRefreshWorkflowError("lifecycle response is not valid JSON") from error
        if not isinstance(payload, Mapping):
            raise ProfileRefreshWorkflowError("lifecycle response is not an object")
        result = dict(payload)
        if not isinstance(result.get("source_url"), str) or not result["source_url"].strip():
            result["source_url"] = current_url
        return result
    raise ProfileRefreshWorkflowError("lifecycle redirects exceed limit")


def fetch_lifecycle_document(
    url: str,
    output: PathInput,
    *,
    timeout_seconds: int = LIFECYCLE_FETCH_TIMEOUT_SECONDS,
) -> Path:
    """Fetch a bounded public HTTPS lifecycle document to a local file."""

    payload = _fetch_lifecycle_payload(url, timeout_seconds=timeout_seconds)
    try:
        destination = Path(os.fspath(output))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        raise ProfileRefreshWorkflowError("lifecycle output cannot be written") from error
    return destination


def _validate_public_url(value: str | None) -> None:
    if not isinstance(value, str) or not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ProfileRefreshWorkflowError("history lifecycle URL is invalid")
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ProfileRefreshWorkflowError("history lifecycle URL is invalid") from error
    if not isinstance(host, str) or not host:
        raise ProfileRefreshWorkflowError("history lifecycle URL is invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        try:
            normalized_host = host.encode("idna").decode("ascii").casefold()
        except UnicodeError as error:
            raise ProfileRefreshWorkflowError("history lifecycle URL is invalid") from error
        if normalized_host.endswith("."):
            normalized_host = normalized_host[:-1]
        labels = normalized_host.split(".")
        if (
            not normalized_host
            or len(normalized_host) > 253
            or any(
                not 1 <= len(label) <= 63
                or _DNS_LABEL_PATTERN.fullmatch(label) is None
                for label in labels
            )
        ):
            raise ProfileRefreshWorkflowError("history lifecycle URL is invalid")
    else:
        normalized_host = host.casefold()
    if (
        normalized_host == "localhost"
        or normalized_host.endswith(".localhost")
        or normalized_host.endswith(".local")
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ProfileRefreshWorkflowError("history lifecycle URL is invalid")
    if port is not None and not 1 <= port <= 65535:
        raise ProfileRefreshWorkflowError("history lifecycle URL is invalid")
    if address is not None and not address.is_global:
        raise ProfileRefreshWorkflowError("history lifecycle URL is invalid")


def _load_report(path: PathInput) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(os.fspath(path)).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ProfileRefreshWorkflowError("batch report is invalid") from error
    if not isinstance(payload, Mapping):
        raise ProfileRefreshWorkflowError("batch report is invalid")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="profile_refresh_workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = subparsers.add_parser("validate-dispatch")
    dispatch.add_argument("--selection-mode", choices=("manual", "history"), required=True)
    dispatch.add_argument("--set-code")
    dispatch.add_argument("--max-environments")
    dispatch.add_argument("--lifecycle-url")
    dispatch.add_argument("--event-format", required=True)
    dispatch.add_argument("--generated-at", required=True)

    lifecycle = subparsers.add_parser("fetch-lifecycle")
    lifecycle.add_argument("--lifecycle-url", required=True)
    lifecycle.add_argument("--output", required=True)
    lifecycle.add_argument("--timeout-seconds", default=str(LIFECYCLE_FETCH_TIMEOUT_SECONDS))

    policy = subparsers.add_parser("validate-cache-policy")
    policy.add_argument("--freshness-days", required=True)
    policy.add_argument("--max-entry-bytes", required=True)
    policy.add_argument("--max-total-bytes", required=True)
    policy.add_argument("--max-records", required=True)
    policy.add_argument("--max-versions-per-source", required=True)

    bundle = subparsers.add_parser("check-report-bundle")
    bundle.add_argument("--report-dir", required=True)
    bundle.add_argument("--max-bytes", default=str(MAX_REPORT_BUNDLE_BYTES))

    summary = subparsers.add_parser("render-summary")
    summary.add_argument("--batch-report", required=True)
    summary.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one helper subcommand with path-free errors."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-dispatch":
            validate_dispatch_inputs(
                args.selection_mode,
                args.set_code,
                args.max_environments,
                args.lifecycle_url,
                args.event_format,
                args.generated_at,
            )
        elif args.command == "fetch-lifecycle":
            fetch_lifecycle_document(
                args.lifecycle_url,
                args.output,
                timeout_seconds=_parse_integer(args.timeout_seconds),
            )
        elif args.command == "validate-cache-policy":
            validate_cache_policy(
                freshness_days=args.freshness_days,
                max_entry_bytes=args.max_entry_bytes,
                max_total_bytes=args.max_total_bytes,
                max_records=args.max_records,
                max_versions_per_source=args.max_versions_per_source,
            )
        elif args.command == "check-report-bundle":
            total = validate_report_bundle(args.report_dir, max_bytes=_parse_integer(args.max_bytes))
            print(total)
        elif args.command == "render-summary":
            summary_text = render_summary(_load_report(args.batch_report))
            Path(os.fspath(args.output)).write_text(summary_text, encoding="utf-8")
        else:
            raise ProfileRefreshWorkflowError("command is invalid")
    except Exception:
        print("profile-refresh-workflow: validation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXPECTED_REPORT_FILES",
    "LIFECYCLE_FETCH_TIMEOUT_SECONDS",
    "MAX_LIFECYCLE_REDIRECTS",
    "MAX_LIFECYCLE_RESPONSE_BYTES",
    "MAX_REPORT_BUNDLE_BYTES",
    "ProfileRefreshWorkflowError",
    "fetch_lifecycle_document",
    "main",
    "render_summary",
    "validate_cache_policy",
    "validate_dispatch_inputs",
    "validate_report_bundle",
]
