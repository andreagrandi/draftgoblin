from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from draftgoblin.carddb import build_card_database_from_bulk_file, refresh_card_database
from draftgoblin.cli import main
from draftgoblin.replay import replay_log_file
from draftgoblin.seventeen import (
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    ColorPairWinRate,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
    save_17lands_format_data,
)

FIXTURE_LOG_PATH = Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)
GOLDEN_REPLAY_PATH = (
    Path(__file__).parent / "golden" / "quick-draft-msh-player.replay.txt"
)


def test_replay_fixture_matches_committed_golden_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cache_fixture_ratings(app_dir=tmp_path)

    exit_code = main(
        argv=[
            "replay",
            str(FIXTURE_LOG_PATH),
            "--bulk-file",
            str(SCRYFALL_BULK_SAMPLE_PATH),
            "--app-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == GOLDEN_REPLAY_PATH.read_text(encoding="utf-8")
    assert captured.err == ""


def test_replay_output_is_byte_identical_across_runs() -> None:
    database = build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)

    ratings_data = _fixture_ratings_data()
    first_output = replay_log_file(
        logfile=FIXTURE_LOG_PATH,
        card_database=database,
        ratings_data=ratings_data,
    )
    second_output = replay_log_file(
        logfile=FIXTURE_LOG_PATH,
        card_database=database,
        ratings_data=ratings_data,
    )

    assert first_output == second_output
    assert first_output == GOLDEN_REPLAY_PATH.read_text(encoding="utf-8")


def test_replay_uses_cached_card_database_without_refreshing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    refresh_card_database(app_dir=tmp_path, bulk_file=SCRYFALL_BULK_SAMPLE_PATH)
    _cache_fixture_ratings(app_dir=tmp_path)

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


def test_replay_without_ratings_cache_uses_neutral_prior_scores(
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
    assert "Data source: neutral prior" in captured.out
    assert "Score" in captured.out
    assert "Prior*" in captured.out
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


def _cache_fixture_ratings(*, app_dir: Path) -> None:
    ratings_data = _fixture_ratings_data()
    save_17lands_format_data(ratings_data.primary, app_dir=app_dir)
    if ratings_data.fallback is not None:
        save_17lands_format_data(ratings_data.fallback, app_dir=app_dir)


def _fixture_ratings_data() -> SeventeenLandsData:
    primary = SeventeenLandsFormatData(
        set_code="MSH",
        event_format=QUICK_DRAFT_FORMAT,
        fetched_at=datetime(2999, 1, 1, tzinfo=UTC),
        card_ratings={
            104894: _stats(
                grp_id=104894,
                name="Fixture Split Card",
                color="WU",
                gih=0.62,
                games_in_hand=900,
                alsa=1.2,
            ),
            105097: _stats(
                grp_id=105097,
                name="Fixture Spider",
                color="G",
                gih=0.60,
                games_in_hand=850,
                alsa=2.1,
            ),
            105134: _stats(
                grp_id=105134,
                name="Fixture Blue Card",
                color="U",
                gih=None,
                games_in_hand=120,
                alsa=4.4,
            ),
            105182: _stats(
                grp_id=105182,
                name="Fixture Final Pick",
                color="R",
                gih=None,
                games_in_hand=80,
                alsa=8.0,
            ),
            105003: _stats(
                grp_id=105003,
                name="Fixture Card 105003",
                color="C",
                gih=0.51,
                games_in_hand=700,
                alsa=6.2,
            ),
            105054: _stats(
                grp_id=105054,
                name="Fixture Card 105054",
                color="C",
                gih=0.50,
                games_in_hand=650,
                alsa=6.8,
            ),
        },
        pair_win_rates=_pair_win_rates(),
    )
    fallback = SeventeenLandsFormatData(
        set_code="MSH",
        event_format=PREMIER_DRAFT_FORMAT,
        fetched_at=datetime(2999, 1, 1, tzinfo=UTC),
        card_ratings={
            105134: _stats(
                grp_id=105134,
                name="Fixture Blue Card",
                color="U",
                gih=0.58,
                games_in_hand=900,
                alsa=3.6,
            ),
            104989: _stats(
                grp_id=104989,
                name="Fixture Card 104989",
                color="C",
                gih=0.57,
                games_in_hand=650,
                alsa=4.2,
            ),
        },
        pair_win_rates=_pair_win_rates(),
    )
    return SeventeenLandsData(
        set_code="MSH",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=primary,
        fallback=fallback,
        thin_sample_minimum=500,
    )


def _stats(
    *,
    grp_id: int,
    name: str,
    color: str,
    gih: float | None,
    games_in_hand: int,
    alsa: float | None,
) -> SeventeenCardStats:
    return SeventeenCardStats(
        grp_id=grp_id,
        name=name,
        color=color,
        rarity="common",
        average_last_seen_at=alsa,
        gih_win_rate=gih,
        opening_hand_win_rate=None,
        drawn_improvement_win_rate=None,
        sample_counts=RatingSampleCounts(
            seen=1000,
            picked=500,
            games_played=games_in_hand,
            opening_hand=200,
            games_in_hand=games_in_hand,
        ),
    )


def _pair_win_rates() -> dict[str, ColorPairWinRate]:
    pairs = ("WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG")
    return {
        pair: ColorPairWinRate(pair=pair, wins=50, games=100, win_rate=0.5)
        for pair in pairs
    }
