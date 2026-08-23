"""Render a Homebrew formula from a published PyPI release.
Resolve the immutable source archive URL and checksum from PyPI metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

PYPI_RELEASE_URL = "https://pypi.org/pypi/draftgoblin/{version}/json"
SHA256_PATTERN = re.compile(pattern=r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SourceDistribution:
    """Describe one immutable source distribution.
    Keep the package URL paired with its SHA-256 digest.
    """

    url: str
    sha256: str


def build_parser() -> argparse.ArgumentParser:
    """Build the formula renderer argument parser.
    Require explicit release, template, and output paths.
    """

    parser = argparse.ArgumentParser(
        description="Render Draftgoblin's Homebrew formula from PyPI metadata.",
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempts", default=12, type=int)
    parser.add_argument("--retry-delay", default=10.0, type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render the formula requested on the command line.
    Return a process-style exit code after writing the output.
    """

    args = build_parser().parse_args(args=argv)
    release_metadata = fetch_release_metadata(
        version=args.version,
        attempts=args.attempts,
        retry_delay=args.retry_delay,
    )
    source_distribution = resolve_source_distribution(
        release_metadata=release_metadata,
        version=args.version,
    )
    render_formula(
        template_path=args.template,
        output_path=args.output,
        source_distribution=source_distribution,
    )
    return 0


def fetch_release_metadata(
    *,
    version: str,
    attempts: int,
    retry_delay: float,
) -> Mapping[str, Any]:
    """Fetch one release's JSON metadata from PyPI.
    Retry briefly while a newly published version propagates.
    """

    if attempts < 1:
        raise ValueError("attempts must be at least one")
    if retry_delay < 0:
        raise ValueError("retry_delay cannot be negative")

    url = PYPI_RELEASE_URL.format(version=version)
    request = urllib.request.Request(
        url=url,
        headers={"User-Agent": "draftgoblin-release-workflow"},
    )
    last_error: urllib.error.URLError | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url=request, timeout=30) as response:
                release_metadata = json.load(fp=response)
            if not isinstance(release_metadata, Mapping):
                raise RuntimeError("PyPI returned non-object release metadata.")
            return release_metadata
        except urllib.error.URLError as error:
            last_error = error
            if attempt == attempts:
                break
            time.sleep(retry_delay)

    raise RuntimeError(
        f"PyPI metadata for draftgoblin {version} was unavailable after "
        f"{attempts} attempts."
    ) from last_error


def resolve_source_distribution(
    *,
    release_metadata: Mapping[str, Any],
    version: str,
) -> SourceDistribution:
    """Resolve the unique source archive in PyPI release metadata.
    Reject missing, ambiguous, or unchecksummed release artifacts.
    """

    info = release_metadata.get("info")
    if not isinstance(info, Mapping) or info.get("version") != version:
        raise RuntimeError("PyPI metadata does not match the requested version.")

    urls = release_metadata.get("urls")
    if not isinstance(urls, list):
        raise RuntimeError("PyPI metadata does not contain a release file list.")

    source_files = [
        release_file
        for release_file in urls
        if isinstance(release_file, Mapping)
        and release_file.get("packagetype") == "sdist"
    ]
    if len(source_files) != 1:
        raise RuntimeError(
            f"Expected one PyPI source distribution, found {len(source_files)}."
        )

    source_file = source_files[0]
    url = source_file.get("url")
    digests = source_file.get("digests")
    sha256 = digests.get("sha256") if isinstance(digests, Mapping) else None
    if not isinstance(url, str) or not url.startswith(
        "https://files.pythonhosted.org/"
    ):
        raise RuntimeError("PyPI source distribution has an unexpected URL.")
    if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
        raise RuntimeError("PyPI source distribution has an invalid SHA-256 digest.")

    return SourceDistribution(url=url, sha256=sha256)


def render_formula(
    *,
    template_path: Path,
    output_path: Path,
    source_distribution: SourceDistribution,
) -> None:
    """Render a formula template with immutable source metadata.
    Write the completed formula to the requested tap path.
    """

    template = Template(template=template_path.read_text(encoding="utf-8"))
    rendered_formula = template.substitute(
        PYPI_URL=source_distribution.url,
        PYPI_SHA256=source_distribution.sha256,
    )
    output_path.write_text(data=rendered_formula, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(argv=None))
