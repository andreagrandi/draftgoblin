from __future__ import annotations

from pathlib import Path

import pytest

from scripts import extract_changelog
from scripts.extract_changelog import ChangelogError, extract_section, main

CHANGELOG = """# Changelog

## [Unreleased]

- Add changelog-backed release notes.

## [1.2.3] - 2026-08-25

- Fix release packaging.

## [Older]

- Keep prior notes.
"""


def test_extract_section_returns_unreleased_body_with_one_trailing_newline() -> None:
    assert extract_section(changelog=CHANGELOG, section="Unreleased") == (
        "- Add changelog-backed release notes.\n"
    )


def test_extract_section_returns_dated_version_body() -> None:
    assert extract_section(changelog=CHANGELOG, section="1.2.3") == (
        "- Fix release packaging.\n"
    )


def test_extract_section_rejects_undated_released_heading() -> None:
    changelog = "## [1.2.3]\n\n- Release note.\n"

    with pytest.raises(ChangelogError, match="valid ISO calendar date"):
        extract_section(changelog=changelog, section="1.2.3")


def test_extract_section_rejects_dated_unreleased_heading() -> None:
    changelog = "## [Unreleased] - 2026-08-25\n\n- Development note.\n"

    with pytest.raises(ChangelogError, match="must not have a date"):
        extract_section(changelog=changelog, section="Unreleased")


def test_extract_section_rejects_invalid_release_date() -> None:
    changelog = "## [1.2.3] - 2026-02-30\n\n- Release note.\n"

    with pytest.raises(ChangelogError, match="invalid ISO calendar date"):
        extract_section(changelog=changelog, section="1.2.3")


def test_extract_section_preserves_first_line_indentation() -> None:
    changelog = "## [Unreleased]\n\n  - Indented note.\n\n"

    assert extract_section(changelog=changelog, section="Unreleased") == (
        "  - Indented note.\n"
    )


def test_extract_section_preserves_markdown_hard_break_spaces() -> None:
    changelog = "## [Unreleased]\n\n- First line.  \n- Second line.\n\n"

    assert extract_section(changelog=changelog, section="Unreleased") == (
        "- First line.  \n- Second line.\n"
    )


@pytest.mark.parametrize(
    ("changelog", "section", "expected_message"),
    [
        (CHANGELOG, "2.0.0", "not found"),
        (
            "## [Unreleased]\n\n- First.\n\n## [Unreleased]\n\n- Second.\n",
            "Unreleased",
            "appears more than once",
        ),
        ("## [Unreleased]\n\n## [1.0.0] - 2026-08-25\n", "Unreleased", "is empty"),
    ],
)
def test_extract_section_rejects_invalid_sections(
    changelog: str,
    section: str,
    expected_message: str,
) -> None:
    with pytest.raises(ChangelogError, match=expected_message):
        extract_section(changelog=changelog, section=section)


@pytest.mark.parametrize(
    "section",
    ["", " Unreleased", "Unreleased ", "bad]name", "bad\nname"],
)
def test_extract_section_rejects_ambiguous_section_names(section: str) -> None:
    with pytest.raises(ChangelogError, match="section"):
        extract_section(changelog=CHANGELOG, section=section)


def test_main_writes_extracted_body_to_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changelog_path = tmp_path / "CHANGELOG.md"
    output_path = tmp_path / "release-notes.md"
    changelog_path.write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.setattr(extract_changelog, "CHANGELOG_PATH", changelog_path)

    exit_code = main(
        argv=["--section", "1.2.3", "--output", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == "- Fix release packaging.\n"
    assert captured.out == ""
    assert captured.err == ""


def test_main_reports_extraction_errors_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changelog_path = tmp_path / "CHANGELOG.md"
    output_path = tmp_path / "release-notes.md"
    changelog_path.write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.setattr(extract_changelog, "CHANGELOG_PATH", changelog_path)

    exit_code = main(
        argv=["--section", "2.0.0", "--output", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error: changelog section '2.0.0' not found" in captured.err
    assert captured.out == ""
    assert not output_path.exists()
