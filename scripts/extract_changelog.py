"""Extract one section from the project changelog.
Provide a reusable function and command-line interface for release workflows.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

CHANGELOG_PATH = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
_SECTION_HEADING_PATTERN = re.compile(pattern=r"\[([^\]\r\n]+)\](.*)")
_DATE_PATTERN = re.compile(pattern=r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_LINE_BREAKS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"


class ChangelogError(ValueError):
    """Describe an invalid or unusable changelog section.
    Keep extraction failures clear for both callers and the command line.
    """


def extract_section(changelog: str, section: str) -> str:
    """Extract one named changelog section.
    Return its non-empty body with exactly one trailing newline.
    """
    _validate_section(section=section)
    lines = changelog.splitlines(keepends=True)
    matches: list[int] = []

    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        heading = line.rstrip(_LINE_BREAKS)
        match = _SECTION_HEADING_PATTERN.fullmatch(heading[3:])
        if match is None or match.group(1) != section:
            continue
        _validate_heading(section=section, suffix=match.group(2))
        matches.append(index)

    if not matches:
        raise ChangelogError(f"changelog section {section!r} not found")
    if len(matches) > 1:
        raise ChangelogError(f"changelog section {section!r} appears more than once")

    start = matches[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break

    body_lines = lines[start:end]
    while body_lines and _is_blank_separator(body_lines[0]):
        body_lines.pop(0)
    while body_lines and _is_blank_separator(body_lines[-1]):
        body_lines.pop()

    body = "".join(body_lines).rstrip(_LINE_BREAKS)
    if not body:
        raise ChangelogError(f"changelog section {section!r} is empty")
    return f"{body}\n"


def _is_blank_separator(line: str) -> bool:
    """Return whether a line is blank separator whitespace.
    Keep non-empty content lines untouched, including their spacing.
    """
    return not line.strip()


def _validate_heading(*, section: str, suffix: str) -> None:
    """Validate the exact heading shape for a requested section.
    Require a valid ISO calendar date for released sections only.
    """
    if section == "Unreleased":
        if suffix:
            raise ChangelogError(
                "malformed changelog heading for section 'Unreleased': "
                "Unreleased must not have a date or trailing heading text"
            )
        return

    if not suffix.startswith(" - "):
        raise ChangelogError(
            f"malformed changelog heading for section {section!r}: "
            "released sections require a valid ISO calendar date"
        )

    date_text = suffix[3:]
    if _DATE_PATTERN.fullmatch(date_text) is None:
        raise ChangelogError(
            f"malformed changelog heading for section {section!r}: "
            f"invalid ISO calendar date {date_text!r}"
        )
    try:
        date.fromisoformat(date_text)
    except ValueError as error:
        raise ChangelogError(
            f"malformed changelog heading for section {section!r}: "
            f"invalid ISO calendar date {date_text!r}"
        ) from error


def build_parser() -> argparse.ArgumentParser:
    """Build the changelog extraction argument parser.
    Require a section name and destination path for release automation.
    """
    parser = argparse.ArgumentParser(description="Extract a changelog section")
    parser.add_argument(
        "--section",
        required=True,
        help="section name inside square brackets",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="output file path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Extract the requested section from the root changelog.
    Return a nonzero status and explain malformed sections on stderr.
    """
    args = build_parser().parse_args(args=argv)
    try:
        changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
        extracted = extract_section(changelog=changelog, section=args.section)
        args.output.write_text(data=extracted, encoding="utf-8")
    except (ChangelogError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _validate_section(section: str) -> None:
    """Validate a section name before constructing its heading.
    Reject delimiters and line breaks that could make heading matching ambiguous.
    """
    if not section or section != section.strip():
        raise ChangelogError(
            "section must be non-empty and have no surrounding whitespace"
        )
    if any(character in section for character in "[]"):
        raise ChangelogError("section must not contain square brackets")
    if any(character in _LINE_BREAKS for character in section):
        raise ChangelogError("section must not contain line breaks")


if __name__ == "__main__":
    raise SystemExit(main(argv=None))
