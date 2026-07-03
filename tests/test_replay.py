from __future__ import annotations

from pathlib import Path

import pytest

from draftgoblin.carddb import build_card_database_from_bulk_file, refresh_card_database
from draftgoblin.cli import main
from draftgoblin.replay import replay_log_file

FIXTURE_LOG_PATH = Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)
GOLDEN_REPLAY_PATH = (
    Path(__file__).parent / "golden" / "quick-draft-msh-player.replay.txt"
)


def test_replay_fixture_matches_committed_golden_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        argv=[
            "replay",
            str(FIXTURE_LOG_PATH),
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == GOLDEN_REPLAY_PATH.read_text(encoding="utf-8")
    assert captured.err == ""


def test_replay_output_is_byte_identical_across_runs() -> None:
    database = build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)

    first_output = replay_log_file(logfile=FIXTURE_LOG_PATH, card_database=database)
    second_output = replay_log_file(logfile=FIXTURE_LOG_PATH, card_database=database)

    assert first_output == second_output
    assert first_output == GOLDEN_REPLAY_PATH.read_text(encoding="utf-8")


def test_replay_uses_cached_card_database_without_refreshing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    refresh_card_database(app_dir=tmp_path, bulk_file=SCRYFALL_BULK_SAMPLE_PATH)

    exit_code = main(
        argv=[
            "replay",
            str(FIXTURE_LOG_PATH),
            "--app-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == GOLDEN_REPLAY_PATH.read_text(encoding="utf-8")
    assert captured.err == ""


def test_replay_without_card_cache_returns_actionable_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        argv=[
            "replay",
            str(FIXTURE_LOG_PATH),
            "--app-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "Run refresh-data first" in captured.err
