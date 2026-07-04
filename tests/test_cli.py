from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from draftgoblin import __version__
from draftgoblin import config
from draftgoblin import cli
from draftgoblin.carddb import CardDatabase
from draftgoblin.cli import build_parser, main
from draftgoblin.pool import DraftState, save_draft_state
from draftgoblin.seventeen import seventeen_lands_structure_targets_cache_path

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
        ("refresh-structure-targets", "17Lands"),
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



def test_watch_tui_once_is_default_mode(
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
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
            "--app-dir",
            str(tmp_path / "app"),
            "--once",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""
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
    assert "Spell selection:" in captured.out
    assert "Selected spells: 4/23" in captured.out
    assert "Card data from 17Lands" in captured.out
    assert captured.err == ""



def test_build_allow_splash_requires_fixing_before_off_pair_cards(
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
            "--allow-splash",
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(tmp_path / "app"),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Splash: enabled but unavailable" in captured.out
    assert "Eligible spells for WU: 4" in captured.out
    assert captured.err == ""



def test_refresh_structure_targets_command_writes_cache(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "app"
    bulk_file = _write_build_bulk_file(directory=tmp_path)
    draft_data_file = _write_structure_draft_data_file(directory=tmp_path)

    exit_code = main(
        argv=[
            "refresh-structure-targets",
            "--set-code",
            "TST",
            "--draft-data-file",
            str(draft_data_file),
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(app_dir),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "pair structure targets" in captured.out
    assert seventeen_lands_structure_targets_cache_path(
        set_code="TST",
        event_format="QuickDraft",
        app_dir=app_dir,
    ).exists()
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
    assert config.DECK_BUILDER.deck_size == 40
    assert config.DECK_BUILDER.target_spell_count == 23
    assert config.DECK_BUILDER.pair_score_card_weight == 0.85
    assert config.DECK_BUILDER.pair_score_win_rate_weight == 0.15
    assert config.DECK_BUILDER.default_land_count == 17
    assert config.DECK_BUILDER.aggressive_land_count == 16
    assert config.DECK_BUILDER.top_heavy_land_count == 18
    assert config.DECK_BUILDER.creature_floor == 14
    assert config.DECK_BUILDER.creature_ceiling == 17
    assert config.DECK_BUILDER.minimum_two_drops == 5
    assert config.DECK_BUILDER.maximum_expensive_spells == 3
    assert config.DECK_BUILDER.two_drop_mana_value == 2.0
    assert config.DECK_BUILDER.expensive_spell_mana_value == 6.0
    assert config.DECK_BUILDER.near_tie_creature_preference_points == 2.0
    assert config.DECK_BUILDER.splash_max_cards == 2
    assert config.DECK_BUILDER.splash_minimum_fixing_sources == 2
    assert config.DECK_BUILDER.splash_elite_score_minimum == 70.0
    assert config.DECK_BUILDER.maximum_unresolved_metadata_ratio == 0.25
    assert config.DECK_BUILDER.main_color_source_floor == 7
    assert config.DECK_BUILDER.structure_maindeck_rate_threshold == 0.5
    assert "minimum two-drop quota" in config.DECK_BUILDER.relaxation_order
    assert config.PICK_ENGINE.thin_sample_minimum == 500
    assert config.PICK_ENGINE.neutral_prior_win_rate == 0.55
    assert config.PICK_ENGINE.score_decimal_places == 0
    assert config.POLL_INTERVAL_SECONDS == 1.0
    assert "WU" in config.COLOR_PAIRS


def test_no_subcommand_defaults_to_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fake_watch(args: object) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "handle_watch", fake_watch)

    exit_code = main(argv=[])

    assert exit_code == 0
    assert len(calls) == 1
    assert getattr(calls[0], "command") == "watch"
    assert getattr(calls[0], "startup_scan") is True


def test_watch_fetches_missing_card_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[Path | None] = []

    def fake_load_or_refresh_card_database(*, app_dir: Path | None = None) -> CardDatabase:
        calls.append(app_dir)
        return CardDatabase(cards={})

    monkeypatch.setattr(
        cli,
        "load_or_refresh_card_database",
        fake_load_or_refresh_card_database,
    )
    log_path = tmp_path / "Player.log"
    log_path.write_text("", encoding="utf-8")
    app_dir = tmp_path / "app"

    exit_code = main(
        argv=[
            "watch",
            "--log-path",
            str(log_path),
            "--plain",
            "--app-dir",
            str(app_dir),
            "--once",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [app_dir]
    assert "Mode: plain-text" in captured.out
    assert captured.err == ""



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



def _write_structure_draft_data_file(*, directory: Path) -> Path:
    path = directory / "draft-data.csv"
    rows = [
        "draft_id,expansion,event_type,event_match_wins,pick,pick_maindeck_rate",
    ]
    names = ["White Fixture", "Blue Fixture", "Second White Fixture", "Second Blue Fixture"]
    for index in range(23):
        rows.append(
            "draft,"
            "TST,"
            "QuickDraft,"
            "7,"
            f"{names[index % len(names)]},"
            "1.0"
        )

    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
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
