from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import NoReturn

import pytest

from draftomen import __version__
from draftomen import config
from draftomen import cli
from draftomen.audit import load_draft_audit_records
from draftomen.carddb import CardDatabase
from draftomen.cli import build_parser, main
from draftomen.pool import DraftState, load_draft_state, save_draft_state
from draftomen.set_profile import (
    SetProfile,
    dump_set_profile,
    load_set_profile,
    set_profile_path,
)
from draftomen.seventeen import (
    QUICK_DRAFT_FORMAT,
    SeventeenLandsError,
    seventeen_lands_structure_targets_cache_path,
)

SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)
QUICK_DRAFT_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
)
FIXTURE_ACCOUNT_ID = "FIXTURECLIENTID1234567890"
FIXTURE_DRAFT_ID = "00000000-0000-4000-8000-000000000004"

PROFILE_GENERATION_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "profile-generation"
)
CLI_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

REFRESH_PLAN_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "refresh-plan"
PROFILE_GENERATION_AT = "2026-08-30T12:00:00+00:00"


def test_package_version_matches_installed_distribution_metadata() -> None:
    assert __version__ == version(distribution_name="draftomen")


def test_tui_version_output_includes_required_disclaimer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv=["--version"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"draftomen-tui {__version__}" in captured.out
    assert (
        "Draft Omen is unofficial Fan Content permitted under the Fan Content Policy."
        in captured.out
    )
    assert "17Lands does not endorse this tool." in captured.out


def test_tui_parser_uses_tui_command_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(args=["--help"])

    captured = capsys.readouterr()

    assert error.value.code == 0
    assert "usage: draftomen-tui" in captured.out
    assert "Unofficial Quick Draft assistant for MTG Arena (TUI)." in captured.out


@pytest.mark.parametrize(
    ("command", "expected_help"),
    [
        ("watch", "Live"),
        ("replay", "Deterministic"),
        ("build", "Select"),
        ("backtest", "Dry-run"),
        ("benchmark-picks", "Offline benchmark"),
        ("refresh-data", "Scryfall"),
        ("refresh-structure-targets", "17Lands"),
        ("generate-profile", "Generate"),
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


def test_profile_manifest_url_is_live_watch_opt_in_only() -> None:
    parser = build_parser()
    watch = parser.parse_args(
        args=["watch", "--profile-manifest-url", "https://profiles.example.test/m.json"]
    )
    assert watch.profile_manifest_url == "https://profiles.example.test/m.json"

    with pytest.raises(SystemExit):
        parser.parse_args(
            args=[
                "replay",
                str(QUICK_DRAFT_FIXTURE_PATH),
                "--profile-manifest-url",
                "https://profiles.example.test/m.json",
            ]
        )


def test_refresh_profile_parser_requires_named_refresh_inputs() -> None:
    parser = build_parser()
    args = parser.parse_args(
        args=[
            "refresh-profile",
            "--set-code",
            "TST",
            "--format",
            QUICK_DRAFT_FORMAT,
            "--manifest-url",
            "https://profiles.example.test/m.json",
            "--app-dir",
            "/tmp/draftomen",
        ]
    )
    assert args.set_code == "TST"
    assert args.format == QUICK_DRAFT_FORMAT
    assert args.manifest_url == "https://profiles.example.test/m.json"
    assert args.app_dir == Path("/tmp/draftomen")


def test_refresh_profile_prints_compact_outcome_and_uses_cached_profile_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeProfileClient:
        def __init__(self, *, app_dir, manifest_url, network_policy) -> None:
            assert app_dir == tmp_path / "app"
            assert manifest_url == "https://profiles.example.test/m.json"
            assert network_policy.value == "allowed"

        def refresh(self, set_code, event_format, *, network_policy):
            calls.append((set_code, event_format))
            return SimpleNamespace(
                profile=SimpleNamespace(
                    set_code="TST",
                    event_format=event_format.casefold(),
                ),
                maturity=SimpleNamespace(value="mature"),
                status="remote-failed",
            )

        def profile_path(self, set_code, event_format):
            return tmp_path / "app" / f"{set_code.casefold()}-{event_format.casefold()}.json"

    monkeypatch.setattr(cli, "ProfileClient", FakeProfileClient)
    args = build_parser().parse_args(
        args=[
            "refresh-profile",
            "--set-code",
            "TST",
            "--format",
            QUICK_DRAFT_FORMAT,
            "--manifest-url",
            "https://profiles.example.test/m.json",
            "--app-dir",
            str(tmp_path / "app"),
        ]
    )

    assert args.handler(args) == 0
    assert calls == [("TST", QUICK_DRAFT_FORMAT)]
    output = capsys.readouterr()
    assert output.err == ""
    assert (
        "refresh-profile: set_code=TST format=quickdraft "
        "maturity=mature outcome=remote-failed "
        f"cache_path={tmp_path / 'app' / 'tst-quickdraft.json'}"
    ) in output.out

def test_corpus_build_cli_local_then_offline_is_byte_stable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_dir = Path(__file__).parent / "fixtures"
    source_spec = tmp_path / "sources.json"
    source_spec.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "sources": [
                    {
                        "name": "scryfall",
                        "kind": "scryfall",
                        "path": str(fixture_dir / "corpus-scryfall.jsonl"),
                    },
                    {
                        "name": "arena-cards",
                        "kind": "arena",
                        "path": str(fixture_dir / "corpus-arena-cards.json"),
                    },
                    {
                        "name": "arena-localization",
                        "kind": "arena",
                        "path": str(fixture_dir / "corpus-arena-localization.json"),
                    },
                    {
                        "name": "mtgjson",
                        "kind": "mtgjson",
                        "path": str(fixture_dir / "corpus-mtgjson.json"),
                    },
                ],
                "selection": {"mode": "explicit", "sets": ["hbl", "dsk"]},
            }
        ),
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    common = [
        "corpus-build",
        "--source-spec",
        str(source_spec),
        "--cache-dir",
        str(cache_dir),
    ]

    assert main([*common, "--output-dir", str(first_dir)]) == 0
    first_output = capsys.readouterr()
    assert "built" in first_output.out
    assert first_output.err == ""

    assert main([*common, "--output-dir", str(second_dir), "--offline"]) == 0
    second_output = capsys.readouterr()
    assert "built" in second_output.out
    assert second_output.err == ""
    assert (first_dir / "normalized.jsonl").read_bytes() == (
        second_dir / "normalized.jsonl"
    ).read_bytes()
    assert (first_dir / "coverage.json").read_bytes() == (
        second_dir / "coverage.json"
    ).read_bytes()


def test_splash_is_default_on_and_each_user_flow_can_disable_it() -> None:
    parser = build_parser()

    assert parser.parse_args(args=["watch"]).splash_enabled is None
    assert parser.parse_args(args=["watch", "--splash"]).splash_enabled is True
    assert parser.parse_args(args=["watch", "--no-splash"]).splash_enabled is False
    assert parser.parse_args(args=["replay", "draft.log"]).splash_enabled is True
    assert (
        parser.parse_args(args=["replay", "draft.log", "--no-splash"]).splash_enabled
        is False
    )
    assert parser.parse_args(args=["build"]).allow_splash is True
    assert parser.parse_args(args=["build", "--no-splash"]).allow_splash is False
    assert parser.parse_args(args=["backtest"]).splash_enabled is True
    assert parser.parse_args(args=["backtest", "--no-splash"]).splash_enabled is False


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


def test_watch_plain_once_ignores_quick_draft_course_snapshot_outside_botdraft(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_path = tmp_path / "Player.log"
    log_path.write_text(
        json.dumps(
            {
                "Course": {
                    "CourseId": "00000000-0000-4000-8000-000000000078",
                    "InternalEventName": "QuickDraft_ABC_20260702",
                    "CurrentModule": "DeckSelect",
                    "ModulePayload": "",
                    "CourseDeckSummary": {"Attributes": []},
                    "CardPool": [],
                    "CardStyles": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

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
    assert "Mode: plain-text" in captured.out
    assert captured.err == ""


def test_watch_plain_actual_entrypoint_processes_complete_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_ratings_load(
        *,
        set_code: str,
        app_dir: Path | None = None,
        progress_callback: object | None = None,
    ) -> NoReturn:
        raise SeventeenLandsError(f"ratings unavailable for {set_code}")

    monkeypatch.setattr(
        cli,
        "load_or_refresh_17lands_data",
        fail_ratings_load,
    )
    log_path = tmp_path / "Player.log"
    log_path.write_text(
        QUICK_DRAFT_FIXTURE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"

    exit_code = main(
        argv=[
            "watch",
            "--log-path",
            str(log_path),
            "--plain",
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
            "--app-dir",
            str(app_dir),
            "--once",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out.count("Pack ") == 42
    assert captured.out.count("Chosen card:") == 42
    assert "Draft complete: 42 cards (explicit completion)" in captured.out
    assert "Suggested deck" in captured.out
    assert (
        "Pool: watch FIXTURECLIENTID1234567890/"
        "00000000-0000-4000-8000-000000000004"
    ) in captured.out
    assert captured.err == ""

    state = load_draft_state(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    )
    assert state.completed is True
    assert state.chosen_pick_count == 42
    assert len(state.pool_grp_ids) == 42
    audit_records = load_draft_audit_records(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    )
    assert len(audit_records) == 86
    assert audit_records[-1]["record_type"] == "draft_completed"


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


def test_watch_mana_icons_flag_is_explicit_tui_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_tui_watch(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_tui_watch", fake_run_tui_watch)

    def fail_if_loaded_before_tui(*, args: object) -> CardDatabase:
        raise AssertionError("card metadata loaded before the TUI started")

    monkeypatch.setattr(cli, "_load_watch_card_database", fail_if_loaded_before_tui)
    log_path = tmp_path / "Player.log"
    log_path.write_text("", encoding="utf-8")

    exit_code = main(
        argv=[
            "watch",
            "--log-path",
            str(log_path),
            "--mana-icons",
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
            "--once",
        ]
    )

    default_args = build_parser().parse_args(args=["watch"])

    assert exit_code == 0
    assert default_args.mana_icons is False
    assert callable(captured["card_database_loader"])
    assert callable(captured["ratings_progress_loader_factory"])
    assert callable(captured["ratings_cache_checker"])
    assert captured["mana_icons_enabled"] is True


def test_watch_tui_ratings_loader_forwards_refresh_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    refresh_values: list[bool] = []

    def fake_run_tui_watch(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    def fake_metadata_loader_factory(**kwargs: object) -> object:
        captured["load_ratings"] = kwargs["load_ratings"]
        return kwargs["load_ratings"]

    def fake_load_or_refresh(
        *,
        set_code: str,
        app_dir: Path | None = None,
        refresh: bool,
        progress_callback: object,
    ) -> object:
        refresh_values.append(refresh)
        return object()

    monkeypatch.setattr(cli, "run_tui_watch", fake_run_tui_watch)
    monkeypatch.setattr(
        cli,
        "metadata_augmenting_ratings_progress_loader",
        fake_metadata_loader_factory,
    )
    monkeypatch.setattr(cli, "load_or_refresh_17lands_data", fake_load_or_refresh)
    log_path = tmp_path / "Player.log"
    log_path.write_text("", encoding="utf-8")

    assert (
        main(
            argv=[
                "watch",
                "--log-path",
                str(log_path),
                "--bulk-file",
                str(SCRYFALL_BULK_SAMPLE_PATH),
                "--once",
            ]
        )
        == 0
    )

    factory = captured["ratings_progress_loader_factory"]
    load_ratings = factory(CardDatabase(cards={}))  # type: ignore[operator]
    load_ratings("TST", lambda progress: None, refresh=False)  # type: ignore[operator]
    load_ratings("TST", lambda progress: None, refresh=True)  # type: ignore[operator]

    assert refresh_values == [False, True]


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
    assert captured.out.startswith("Suggested deck\n")
    assert "Color pair: WU (automatic" in captured.out
    assert "Average mana value:" in captured.out
    assert "Mana curve: 0:" in captured.out
    assert "Chosen pair: WU (automatic" in captured.out
    assert "Runner-up:" in captured.out
    assert "Strength gap:" in captured.out
    assert "Structure checks:" in captured.out
    assert "Selected spells: 4/23" in captured.out
    assert "Card data from 17Lands" in captured.out
    assert captured.err == ""


def test_build_cli_loads_local_profile_and_passes_it_to_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "app"
    profile = load_set_profile(
        Path(__file__).parent / "fixtures" / "set-profiles" / "mature.json",
        expected_set_code="TST",
        expected_format=QUICK_DRAFT_FORMAT,
    )
    dump_set_profile(
        profile,
        set_profile_path(
            set_code="TST",
            event_format=QUICK_DRAFT_FORMAT,
            app_dir=app_dir,
        ),
    )
    bulk_file = _write_build_bulk_file(directory=tmp_path)
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(
        json.dumps({"set_code": "TST", "pool_grp_ids": [1, 2, 3, 4, 5]}),
        encoding="utf-8",
    )

    loaded_profiles: list[SetProfile | None] = []
    real_load = cli.load_scoring_profile

    def record_load(
        set_code: str,
        event_format: str,
        *,
        app_dir: Path | None = None,
        **kwargs: object,
    ) -> SetProfile | None:
        loaded = real_load(
            set_code=set_code,
            event_format=event_format,
            app_dir=app_dir,
            **kwargs,
        )
        loaded_profiles.append(loaded)
        return loaded

    builder_kwargs: dict[str, object] = {}

    def record_build(**kwargs: object) -> tuple[object, SimpleNamespace]:
        builder_kwargs.update(kwargs)
        return object(), SimpleNamespace(
            spell_selection=object(),
            mana_base=object(),
        )

    monkeypatch.setattr(cli, "load_scoring_profile", record_load)
    monkeypatch.setattr(cli, "build_deck_from_pool", record_build)
    monkeypatch.setattr(cli, "format_build_result", lambda **kwargs: "stub build\n")

    exit_code = main(
        argv=[
            "build",
            "--pool",
            str(pool_file),
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(app_dir),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert loaded_profiles == [profile]
    assert builder_kwargs["set_profile"] is loaded_profiles[0]
    assert captured.out == "stub build\n"
    assert captured.err == ""


def test_build_defaults_to_splash_but_requires_an_eligible_card(
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
    assert "Splash: enabled; no eligible A- or better" in captured.out
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


def _run_generate_profile_cli(
    *,
    stage: str,
    output_dir: Path,
    card_database_path: Path = PROFILE_GENERATION_FIXTURE_DIR / "card-database.json",
    ratings_path: Path | None = None,
    source_manifest_path: Path | None = None,
    generated_at: str = PROFILE_GENERATION_AT,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "draftomen.cli",
        "generate-profile",
        "--set-code",
        "TST",
        "--format",
        "quickdraft",
        "--stage",
        stage,
        "--generated-at",
        generated_at,
        "--card-database-file",
        str(card_database_path),
        "--output-dir",
        str(output_dir),
    ]
    if ratings_path is not None:
        command.extend(["--ratings-file", str(ratings_path)])
    if source_manifest_path is not None:
        command.extend(["--source-manifest", str(source_manifest_path)])
    return subprocess.run(
        command,
        cwd=CLI_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def _assert_profile_cli_success(
    *,
    completed: subprocess.CompletedProcess[str],
    expected_input_count: int,
    expected_stage: str,
) -> tuple[Path, Path, bytes, bytes]:
    assert completed.returncode == 0
    assert completed.stderr == ""

    report_lines = completed.stdout.splitlines()
    assert len(report_lines) == 8
    reported = dict(line.split("=", maxsplit=1) for line in report_lines)
    assert set(reported) == {
        "maturity",
        "input_count",
        "sample_count",
        "skip_count",
        "error_count",
        "validation",
        "artifact",
        "generation_manifest",
    }

    artifact_path = Path(reported["artifact"])
    manifest_path = Path(reported["generation_manifest"])
    artifact_bytes = artifact_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    report = json.loads(manifest_bytes)
    profile_bytes = gzip.decompress(artifact_bytes)
    profile = SetProfile.from_json(json.loads(profile_bytes))

    assert profile.set_code == "tst"
    assert profile.event_format == "quickdraft"
    assert profile.maturity.value == reported["maturity"]
    assert report["stage"] == expected_stage
    assert report["set_code"] == "tst"
    assert report["event_format"] == "quickdraft"
    assert report["profile_sha256"] == hashlib.sha256(profile_bytes).hexdigest()
    assert report["gzip_sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert artifact_path.name == f"{report['gzip_sha256']}.json.gz"
    assert manifest_bytes == (
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    expected_reported = {
        "maturity": profile.maturity.value,
        "input_count": str(expected_input_count),
        "sample_count": str(report["samples"]["total"]),
        "skip_count": str(sum(report["skip_reasons"].values())),
        "error_count": str(sum(report["error_reasons"].values())),
        "validation": "passed",
        "artifact": str(artifact_path),
        "generation_manifest": str(manifest_path),
    }
    assert reported == expected_reported
    assert completed.stdout == "".join(
        f"{key}={expected_reported[key]}\n"
        for key in (
            "maturity",
            "input_count",
            "sample_count",
            "skip_count",
            "error_count",
            "validation",
            "artifact",
            "generation_manifest",
        )
    )
    assert not any(
        secret in completed.stdout
        for secret in ("alpha", "beta", "Support Creature", "Removal Spell", "FIXTURECLIENTID")
    )
    return artifact_path, manifest_path, artifact_bytes, manifest_bytes


@pytest.mark.parametrize(
    ("stage", "expected_input_count", "source_manifest_name", "with_ratings"),
    [
        ("metadata", 2, "manifest-no-data.json", False),
        ("early", 3, "manifest-early-data.json", True),
        ("mature", 3, "manifest-mature-data.json", True),
    ],
)
def test_generate_profile_cli_processes_all_stages_deterministically(
    stage: str,
    expected_input_count: int,
    source_manifest_name: str,
    with_ratings: bool,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "published"
    source_manifest_path = PROFILE_GENERATION_FIXTURE_DIR / source_manifest_name
    ratings_path = (
        PROFILE_GENERATION_FIXTURE_DIR / "ratings.json" if with_ratings else None
    )

    first = _run_generate_profile_cli(
        stage=stage,
        output_dir=output_dir,
        ratings_path=ratings_path,
        source_manifest_path=source_manifest_path,
    )
    first_artifact, first_manifest, first_artifact_bytes, first_manifest_bytes = (
        _assert_profile_cli_success(
            completed=first,
            expected_input_count=expected_input_count,
            expected_stage=stage,
        )
    )

    second = _run_generate_profile_cli(
        stage=stage,
        output_dir=output_dir,
        ratings_path=ratings_path,
        source_manifest_path=source_manifest_path,
    )
    second_artifact, second_manifest, second_artifact_bytes, second_manifest_bytes = (
        _assert_profile_cli_success(
            completed=second,
            expected_input_count=expected_input_count,
            expected_stage=stage,
        )
    )

    assert second.stdout == first.stdout
    assert second_artifact == first_artifact
    assert second_manifest == first_manifest
    assert second_artifact_bytes == first_artifact_bytes
    assert second_manifest_bytes == first_manifest_bytes
    assert hashlib.sha256(second_artifact_bytes).hexdigest() == Path(
        second_artifact
    ).name.removesuffix(".json.gz")


def test_generate_profile_cli_failure_preserves_last_valid_publication(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "published"
    first = _run_generate_profile_cli(
        stage="early",
        output_dir=output_dir,
        ratings_path=PROFILE_GENERATION_FIXTURE_DIR / "ratings.json",
        source_manifest_path=PROFILE_GENERATION_FIXTURE_DIR / "manifest-early-data.json",
    )
    artifact_path, manifest_path, artifact_bytes, manifest_bytes = (
        _assert_profile_cli_success(
            completed=first,
            expected_input_count=3,
            expected_stage="early",
        )
    )

    failed = _run_generate_profile_cli(
        stage="early",
        output_dir=output_dir,
        card_database_path=tmp_path / "missing-card-database.json",
        ratings_path=PROFILE_GENERATION_FIXTURE_DIR / "ratings.json",
        source_manifest_path=PROFILE_GENERATION_FIXTURE_DIR / "manifest-early-data.json",
    )
    assert failed.returncode == 1
    assert failed.stdout == ""
    assert failed.stderr == (
        "generate-profile: Could not load the card database input.\n"
    )
    assert "Support Creature" not in failed.stderr
    assert "alpha" not in failed.stderr
    assert artifact_path.read_bytes() == artifact_bytes
    assert manifest_path.read_bytes() == manifest_bytes


@pytest.mark.parametrize(
    ("generated_at", "expected_error"),
    [
        (
            "2026-08-30T12:00:00",
            "--generated-at must include a timezone offset.",
        ),
        (
            "0001-01-01T00:00:00+14:00",
            "--generated-at must be representable after UTC normalization.",
        ),
        (
            "not-an-iso-timestamp",
            "--generated-at must be a valid ISO-8601 timestamp.",
        ),
    ],
)
def test_generate_profile_cli_rejects_invalid_timestamp_as_argparse_error(
    generated_at: str,
    expected_error: str,
) -> None:
    completed = _run_generate_profile_cli(
        stage="metadata",
        output_dir=Path("/tmp/draftomen-profile-cli-invalid"),
        generated_at=generated_at,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "Traceback" not in completed.stderr
    assert (
        "draftomen-tui generate-profile: error: argument --generated-at: "
        f"{expected_error}"
    ) in completed.stderr.splitlines()
    assert expected_error in completed.stderr
    assert not completed.stderr.startswith("generate-profile:")


def test_plan_profile_refresh_cli_dry_run_prints_canonical_plan_without_profiles(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "draftomen.cli",
            "plan-profile-refresh",
            "--set-code",
            "new",
            "--event-format",
            "PremierDraft",
            "--inventory-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "expansions.json"),
            "--lifecycle-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "lifecycle.json"),
            "--dry-run",
        ],
        cwd=CLI_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    parsed = json.loads(completed.stdout)
    assert parsed["selection"] == {"mode": "manual", "set_code": "NEW"}
    assert parsed["event_format"] == "premierdraft"
    assert parsed["environments"] == [
        {
            "event_format": "premierdraft",
            "lifecycle": "active",
            "reasons": ["manual"],
            "set_code": "NEW",
        }
    ]
    assert completed.stdout.endswith("\n")
    assert parsed["inventory"]["source_url"] == "https://www.17lands.com/data/expansions"
    assert str(REFRESH_PLAN_FIXTURE_DIR) not in completed.stdout
    assert "file://" not in completed.stdout
    assert parsed["lifecycle"]["source_url"] == "https://schedule.example.test/arena.json"
    assert (
        "inventory:duplicate-entry:entry=NEW:normalized expansion code already present"
        in parsed["diagnostics"]
    )
    assert "inventory:malformed-entry:entry=:expected a non-empty string" in parsed["diagnostics"]
    assert not list(tmp_path.glob("**/*profile*"))


def test_plan_profile_refresh_cli_output_plan_writes_without_printing(
    tmp_path: Path,
) -> None:
    output_plan = tmp_path / "refresh-plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "draftomen.cli",
            "plan-profile-refresh",
            "--set-code",
            "new",
            "--event-format",
            "PremierDraft",
            "--inventory-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "expansions.json"),
            "--lifecycle-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "lifecycle.json"),
            "--output-plan",
            str(output_plan),
        ],
        cwd=CLI_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert json.loads(output_plan.read_text(encoding="utf-8"))["selection"] == {
        "mode": "manual",
        "set_code": "NEW",
    }
    assert output_plan.read_text(encoding="utf-8").endswith("\n")
    assert not list(tmp_path.glob("**/*profile*"))


def test_plan_profile_refresh_cli_requires_exactly_one_output_mode() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "plan-profile-refresh",
                "--set-code",
                "TST",
                "--event-format",
                "QuickDraft",
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "plan-profile-refresh",
                "--set-code",
                "TST",
                "--event-format",
                "QuickDraft",
                "--dry-run",
                "--output-plan",
                "plan.json",
            ]
        )


def test_plan_profile_refresh_cli_rejects_empty_automatic_selection(tmp_path: Path) -> None:
    lifecycle_file = tmp_path / "lifecycle.json"
    lifecycle_file.write_text(
        json.dumps(
            {
                "provider": "Arena schedule",
                "source_url": "https://schedule.example.test/arena.json",
                "version": "2026-08-30",
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "draftomen.cli",
            "plan-profile-refresh",
            "--active",
            "--event-format",
            "QuickDraft",
            "--inventory-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "expansions.json"),
            "--lifecycle-file",
            str(lifecycle_file),
            "--dry-run",
        ],
        cwd=CLI_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "active selection matched no environments" in completed.stderr



def test_plan_profile_refresh_cli_rejects_unknown_manual_selection() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "draftomen.cli",
            "plan-profile-refresh",
            "--set-code",
            "NOT-IN-17LANDS",
            "--event-format",
            "QuickDraft",
            "--inventory-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "expansions.json"),
            "--lifecycle-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "lifecycle.json"),
            "--dry-run",
        ],
        cwd=CLI_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "NOT-IN-17LANDS" in completed.stderr
    assert "not present in the 17Lands inventory" in completed.stderr


def test_plan_profile_refresh_cli_requires_one_selection_and_event_format() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["plan-profile-refresh", "--event-format", "QuickDraft", "--dry-run"])
    with pytest.raises(SystemExit):
        parser.parse_args(["plan-profile-refresh", "--active", "--dry-run"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["plan-profile-refresh", "--active", "--event-format", "QuickDraft", "--set-code", "TST", "--dry-run"]
        )



@pytest.mark.parametrize("lifecycle_url", ["", "not-a-url"])
def test_plan_profile_refresh_cli_rejects_malformed_lifecycle_url_without_traceback(
    lifecycle_url: str,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "draftomen.cli",
            "plan-profile-refresh",
            "--set-code",
            "NEW",
            "--event-format",
            "PremierDraft",
            "--inventory-file",
            str(REFRESH_PLAN_FIXTURE_DIR / "expansions.json"),
            "--lifecycle-url",
            lifecycle_url,
            "--dry-run",
        ],
        cwd=CLI_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "Traceback" not in completed.stderr
    assert completed.stderr.strip() == (
        "plan-profile-refresh: lifecycle URL must be an absolute URL"
    )
