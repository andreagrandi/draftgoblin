from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from draftgoblin import __version__
from draftgoblin import config
from draftgoblin.cli import build_parser, main
from draftgoblin.pool import DraftState, save_draft_state

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
        ("build", "Select"),
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



def test_build_pool_file_selects_pair_offline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bulk_file = _write_build_bulk_file(directory=tmp_path)
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(
        json.dumps({"set_code": "TST", "pool_grp_ids": [1, 2, 3, 4, 5]}),
        encoding="utf-8",
    )

    exit_code = main(
        argv=[
            "build",
            "--pool",
            str(pool_file),
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(tmp_path / "app"),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Deck builder pair selection" in captured.out
    assert "Chosen pair: WU (automatic" in captured.out
    assert "Runner-up:" in captured.out
    assert "Score gap:" in captured.out
    assert "Card data from 17Lands" in captured.out
    assert captured.err == ""



def test_build_pair_flag_forces_requested_pair(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bulk_file = _write_build_bulk_file(directory=tmp_path)
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(
        json.dumps({"set_code": "TST", "pool_grp_ids": [1, 2, 3, 4, 5]}),
        encoding="utf-8",
    )

    exit_code = main(
        argv=[
            "build",
            "--pool",
            str(pool_file),
            "--pair",
            "BR",
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(tmp_path / "app"),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Chosen pair: BR (forced" in captured.out
    assert "Best automatic pair: WU" in captured.out
    assert captured.err == ""



def test_build_uses_persisted_pool_with_account_and_draft_filters(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "app"
    bulk_file = _write_build_bulk_file(directory=tmp_path)
    save_draft_state(
        state=_build_draft_state(account_id="acct", draft_id="draft"),
        app_dir=app_dir,
    )

    exit_code = main(
        argv=[
            "build",
            "--account",
            "acct",
            "--draft-id",
            "draft",
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(app_dir),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Pool: persisted acct/draft" in captured.out
    assert "Chosen pair: WU (automatic" in captured.out
    assert captured.err == ""



def test_build_rejects_invalid_pair(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(argv=["build", "--pair", "ZZ"])

    captured = capsys.readouterr()

    assert error.value.code == 2
    assert "invalid choice" in captured.err


def test_config_exposes_documented_tunables() -> None:
    assert config.DECK_BUILDER.target_spell_count == 23
    assert config.DECK_BUILDER.pair_score_card_weight == 0.85
    assert config.DECK_BUILDER.pair_score_win_rate_weight == 0.15
    assert config.DECK_BUILDER.default_land_count == 17
    assert config.PICK_ENGINE.thin_sample_minimum == 500
    assert config.PICK_ENGINE.neutral_prior_win_rate == 0.55
    assert config.PICK_ENGINE.score_decimal_places == 0
    assert config.POLL_INTERVAL_SECONDS == 1.0
    assert "WU" in config.COLOR_PAIRS


def test_parser_returns_help_when_no_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(argv=[])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "watch" in captured.out
    assert "refresh-data" in captured.out



def _write_build_bulk_file(*, directory: Path) -> Path:
    path = directory / "build-bulk.jsonl"
    rows = [
        _scryfall_row(grp_id=1, name="White Fixture", colors=["W"]),
        _scryfall_row(grp_id=2, name="Blue Fixture", colors=["U"]),
        _scryfall_row(grp_id=3, name="Second White Fixture", colors=["W"]),
        _scryfall_row(grp_id=4, name="Second Blue Fixture", colors=["U"]),
        _scryfall_row(grp_id=5, name="Red Fixture", colors=["R"]),
    ]
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )
    return path



def _scryfall_row(*, grp_id: int, name: str, colors: list[str]) -> dict[str, object]:
    return {
        "arena_id": grp_id,
        "name": name,
        "colors": colors,
        "cmc": 2,
        "rarity": "common",
        "type_line": "Creature — Fixture",
    }



def _build_draft_state(*, account_id: str, draft_id: str) -> DraftState:
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC).isoformat()
    return DraftState(
        account_id=account_id,
        draft_id=draft_id,
        event_name="QuickDraft_TST_20260703",
        set_code="TST",
        course_id=draft_id,
        started_at=now,
        updated_at=now,
        completed_at=None,
        completed=False,
        picks=(),
        pool_grp_ids=(1, 2, 3, 4, 5),
    )
