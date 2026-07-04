from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from textual.widgets import DataTable, Static

from draftgoblin.carddb import CardDatabase, build_card_database_from_bulk_file
from draftgoblin.seventeen import (
    QUICK_DRAFT_FORMAT,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
)
from draftgoblin.tui import DraftgoblinTuiApp

FIXTURE_LOG_PATH = Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)


def test_tui_fixture_stream_updates_pack_panel_and_status_bar(tmp_path: Path) -> None:
    asyncio.run(_assert_fixture_stream_updates_pack_panel(tmp_path=tmp_path))


async def _assert_fixture_stream_updates_pack_panel(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        table = app.query_one("#pack-table", DataTable)
        rows = [table.get_row_at(index) for index in range(table.row_count)]
        status = _status_text(app=app)

        assert table.row_count == 14
        assert any("Fixture Spider" in row for row in _card_cells(rows=rows))
        assert "Account: FixturePlayer" in status
        assert "Pair: open" in status
        assert "Pick: P1P1" in status
        assert "Pool: 0" in status
        assert "Data: neutral prior" in status
        assert "Card data from 17Lands (17lands.com)" in status


def test_tui_keybindings_toggle_columns_and_cycle_sort(tmp_path: Path) -> None:
    asyncio.run(_assert_keybindings_toggle_columns_and_sort(tmp_path=tmp_path))


async def _assert_keybindings_toggle_columns_and_sort(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        assert app.visible_column_keys == (
            "rank",
            "score",
            "card",
            "colors",
            "fit",
            "gih",
            "alsa",
            "mv",
            "source",
        )

        await pilot.press("c")
        assert app.visible_column_keys == ("rank", "score", "card", "colors")

        await pilot.press("s")
        assert app.sort_mode == "alsa"
        assert "Sort: ALSA" in _status_text(app=app)

        await pilot.press("s")
        assert app.sort_mode == "mv"
        assert "Sort: MV" in _status_text(app=app)

        await pilot.press("q")


def test_tui_narrow_width_hides_secondary_columns_first(tmp_path: Path) -> None:
    asyncio.run(_assert_narrow_width_hides_secondary_columns(tmp_path=tmp_path))


async def _assert_narrow_width_hides_secondary_columns(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(60, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        assert app.visible_column_keys == ("rank", "score", "card", "colors")


def test_tui_slow_ratings_refresh_stays_responsive(tmp_path: Path) -> None:
    asyncio.run(_assert_slow_ratings_refresh_stays_responsive(tmp_path=tmp_path))


async def _assert_slow_ratings_refresh_stays_responsive(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_loader(set_code: str) -> SeventeenLandsData:
        started.set()
        release.wait(timeout=0.5)
        return _ratings_data(set_code=set_code)

    app = _tui_app(tmp_path=tmp_path, ratings_loader=slow_loader)

    async with app.run_test(size=(120, 24)) as pilot:
        try:
            start = time.monotonic()
            app.process_lines(lines=_first_pack_lines())
            elapsed = time.monotonic() - start
            await pilot.pause()

            assert elapsed < 0.25
            assert await asyncio.to_thread(started.wait, 0.5)
            assert "MSH" in app.loading_rating_sets

            await pilot.press("s")
            assert app.sort_mode == "alsa"

            release.set()
            for _ in range(10):
                await pilot.pause(0.05)
                if "MSH" not in app.loading_rating_sets:
                    break

            assert "MSH" not in app.loading_rating_sets
        finally:
            release.set()


def _tui_app(
    *,
    tmp_path: Path,
    ratings_loader: Callable[[str], SeventeenLandsData] | None = None,
) -> DraftgoblinTuiApp:
    return DraftgoblinTuiApp(
        log_path=tmp_path / "Player.log",
        card_database=_fixture_card_database(),
        app_dir=tmp_path / "app",
        ratings_loader=ratings_loader,
        poll_enabled=False,
    )


def _fixture_card_database() -> CardDatabase:
    return build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)


def _first_pack_lines() -> list[str]:
    return FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()[:7]


def _status_text(*, app: DraftgoblinTuiApp) -> str:
    status = app.query_one("#status-bar", Static)
    return str(status.render())


def _card_cells(*, rows: list[list[object]]) -> list[str]:
    return [str(row[2]) for row in rows]


def _ratings_data(*, set_code: str) -> SeventeenLandsData:
    primary = SeventeenLandsFormatData(
        set_code=set_code,
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
        },
        pair_win_rates={},
    )
    return SeventeenLandsData(
        set_code=set_code,
        requested_format=QUICK_DRAFT_FORMAT,
        primary=primary,
        fallback=None,
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
