from __future__ import annotations

from pathlib import Path

import pytest

from draftgoblin import __version__
from draftgoblin import config
from draftgoblin.cli import build_parser, main

SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)


def test_version_output_includes_required_disclaimer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv=["--version"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"draftgoblin {__version__}" in captured.out
    assert (
        "Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy."
        in captured.out
    )
    assert "17Lands does not endorse this tool." in captured.out


@pytest.mark.parametrize(
    ("command", "expected_help"),
    [
        ("watch", "Live"),
        ("replay", "Deterministic"),
        ("build", "Stub"),
        ("refresh-data", "Scryfall"),
    ],
)
def test_subcommands_are_registered_with_help_text(
    command: str,
    expected_help: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as error:
        parser.parse_args(args=[command, "--help"])

    captured = capsys.readouterr()

    assert error.value.code == 0
    assert command in captured.out
    assert expected_help in captured.out


def test_watch_plain_once_honors_log_path_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_path = tmp_path / "Player.log"
    log_path.write_text("", encoding="utf-8")

    exit_code = main(
        argv=[
            "watch",
            "--log-path",
            str(log_path),
            "--plain",
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
            "--app-dir",
            str(tmp_path / "app"),
            "--once",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert str(log_path) in captured.out
    assert "Mode: plain-text" in captured.out
    assert captured.err == ""


def test_config_exposes_documented_tunables() -> None:
    assert config.DECK_BUILDER.target_spell_count == 23
    assert config.DECK_BUILDER.default_land_count == 17
    assert config.PICK_ENGINE.thin_sample_minimum == 500
    assert config.POLL_INTERVAL_SECONDS == 1.0
    assert "WU" in config.COLOR_PAIRS


def test_parser_returns_help_when_no_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(argv=[])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "watch" in captured.out
    assert "refresh-data" in captured.out
