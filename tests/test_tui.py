from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from draftgoblin.carddb import CardDatabase, build_card_database_from_bulk_file
from draftgoblin.pool import DraftState, save_draft_state
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


def test_tui_warns_when_card_metadata_is_incomplete(tmp_path: Path) -> None:
    asyncio.run(_assert_tui_warns_when_card_metadata_is_incomplete(tmp_path=tmp_path))


async def _assert_tui_warns_when_card_metadata_is_incomplete(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path, card_database=CardDatabase(cards={}))

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        assert "Warning: 14 unresolved card metadata" in _status_text(app=app)
        assert "Metadata warning: 14 unresolved card metadata" in _pool_summary_text(
            app=app,
        )


def test_tui_build_view_refuses_unknown_metadata_pool(tmp_path: Path) -> None:
    asyncio.run(_assert_build_view_refuses_unknown_metadata_pool(tmp_path=tmp_path))


async def _assert_build_view_refuses_unknown_metadata_pool(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path, card_database=CardDatabase(cards={}))

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        await pilot.press("b")
        await pilot.pause()

        title = app.query_one("#pack-title", Static)
        assert str(title.render()).startswith("Build view unavailable")
        assert "automatic" not in str(title.render())
        assert "Override: unavailable" in _pool_summary_text(app=app)
        assert "Build view unavailable: Card metadata is missing" in app.build_view_text
        assert "The build cannot be trusted" in app.build_view_text
        assert "no deck was produced" in app.build_view_text
        assert "Picked pool (1)" in app.build_view_text
        assert "[unresolved] Unknown card 105097 (grpId 105097)" in app.build_view_text
        assert "Build sheet:" not in app.build_view_text
        assert "Build: Card metadata is missing" in _status_text(app=app)
        assert "Build action: cannot build — Card metadata is missing" in _status_text(
            app=app,
        )


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


def test_tui_sidebar_updates_pool_distribution_curve_and_last_picks(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_sidebar_updates_pool_summary(tmp_path=tmp_path))


async def _assert_sidebar_updates_pool_summary(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        summary = _pool_summary_text(app=app)
        last_picks = _last_picks_text(app=app)

        assert "Pool size: 1" in summary
        assert "Colors:" in summary
        assert "G █████ 1" in summary
        assert "Set: MSH — Marvel Super Heroes" in summary
        assert "Curve:" in summary
        assert "4█1" in summary
        assert "Fixture Spider" in last_picks


def test_tui_build_view_lists_full_picked_pool_with_card_details(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_build_view_lists_full_picked_pool(tmp_path=tmp_path))


async def _assert_build_view_lists_full_picked_pool(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)
    save_draft_state(
        state=_draft_state(
            account_id="FIXTURECLIENTID1234567890",
            draft_id="pool-details-draft",
            event_name="QuickDraft_MSH_20260702",
            pool_grp_ids=(104894, 105097),
        ),
        app_dir=tmp_path / "app",
    )

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_account_lines())
        await pilot.pause()

        assert "Picked pool" not in app.build_view_text

        await pilot.press("c")
        await pilot.pause()

        text = app.build_view_text
        assert "Picked pool (2)" in text
        assert "01. Fixture Split Card | Colors WU | MV 3" in text
        assert "02. Fixture Spider | Colors G | MV 4" in text


def test_tui_build_keybinding_opens_build_view_on_demand(tmp_path: Path) -> None:
    asyncio.run(_assert_build_keybinding_opens_build_view(tmp_path=tmp_path))


async def _assert_build_keybinding_opens_build_view(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        await pilot.press("b")
        await pilot.pause()

        title = app.query_one("#pack-title", Static)
        assert str(title.render()).startswith("Build view — pair")
        assert app.build_view_text.startswith("Suggested deck\n")
        assert "Set: MSH — Marvel Super Heroes" in app.build_view_text
        assert "Pool size: 1 cards" in app.build_view_text
        assert "Average mana value:" in app.build_view_text
        assert "Mana curve: 0:" in app.build_view_text
        assert "Selected spells by mana value" in app.build_view_text
        assert "Picked pool" not in app.build_view_text
        assert "Color-pair reasoning" not in app.build_view_text
        assert "Pair scores" not in app.build_view_text
        assert "Details hidden: press c" in app.build_view_text
        assert "Build action: rebuilt current pool" in _status_text(app=app)

        await pilot.press("c")
        await pilot.pause()

        assert "Picked pool (1)" in app.build_view_text
        assert "01. Fixture Spider | Colors G | MV 4" in app.build_view_text

        await pilot.press("b")
        await pilot.pause()

        assert "Build action: no build needed — current pool already shown" in _status_text(
            app=app,
        )


def test_tui_completion_switches_to_build_view_and_pair_override_keybinding(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_completion_build_view_and_pair_override(tmp_path=tmp_path))


async def _assert_completion_build_view_and_pair_override(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_full_fixture_lines())
        await pilot.pause()

        title = app.query_one("#pack-title", Static)
        table = app.query_one("#pack-table", DataTable)
        build_scroll = app.query_one("#build-scroll", VerticalScroll)

        assert str(title.render()).startswith("Build view — pair WU (automatic)")
        assert table.display is False
        assert build_scroll.display is True
        assert app.build_view_text.startswith("Suggested deck\n")
        assert "Selected spells by mana value" in app.build_view_text
        assert "Color pair: WU (automatic" in app.build_view_text
        assert "Average mana value:" in app.build_view_text
        assert "Mana curve: 0:" in app.build_view_text

        await pilot.press("s")
        await pilot.pause()

        assert "Selected spells by score" in app.build_view_text
        assert "Build sort: score" in _status_text(app=app)

        await pilot.press("c")
        await pilot.pause()

        assert "Color-pair reasoning" in app.build_view_text
        assert "top 23 sum" in app.build_view_text
        assert "Bench" in app.build_view_text

        await pilot.press("pagedown")
        await pilot.pause()

        assert build_scroll.scroll_y > 0

        await pilot.press("p")
        await pilot.pause()

        assert str(title.render()).startswith("Build view — pair WB (forced WB)")
        assert "Color pair: WB (forced" in app.build_view_text
        assert "Override: WB" in _status_text(app=app)


def test_tui_account_event_recovers_latest_persisted_state(tmp_path: Path) -> None:
    asyncio.run(_assert_account_event_recovers_latest_persisted_state(tmp_path=tmp_path))


async def _assert_account_event_recovers_latest_persisted_state(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)
    save_draft_state(
        state=_draft_state(
            account_id="FIXTURECLIENTID1234567890",
            draft_id="recovered-draft",
            event_name="QuickDraft_MSH_20260702",
            pool_grp_ids=(104894, 105097),
        ),
        app_dir=tmp_path / "app",
    )

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_account_lines())
        await pilot.pause()

        assert "Account: FixturePlayer" in _status_text(app=app)
        assert "Draft: recovered-draft" in app.build_view_text
        assert "Pool size: 2 cards" in app.build_view_text


def test_tui_account_key_cycles_recovered_drafts(tmp_path: Path) -> None:
    asyncio.run(_assert_account_key_cycles_recovered_drafts(tmp_path=tmp_path))


async def _assert_account_key_cycles_recovered_drafts(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)
    save_draft_state(
        state=_draft_state(
            account_id="test-account",
            account_screen_name="TestUser",
            draft_id="test-draft",
            event_name="QuickDraft_MSH_20260702",
            pool_grp_ids=(104894, 105097),
        ),
        app_dir=tmp_path / "app",
    )

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_full_fixture_lines())
        await pilot.pause()

        assert "Account: FixturePlayer" in _status_text(app=app)

        await pilot.press("a")
        await pilot.pause()

        assert "Account: TestUser (test-account)" in _status_text(app=app)
        assert "Draft: test-draft" in app.build_view_text
        assert "Pool size: 2 cards" in app.build_view_text


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
    card_database: CardDatabase | None = None,
    ratings_loader: Callable[[str], SeventeenLandsData] | None = None,
) -> DraftgoblinTuiApp:
    return DraftgoblinTuiApp(
        log_path=tmp_path / "Player.log",
        card_database=(
            card_database if card_database is not None else _fixture_card_database()
        ),
        app_dir=tmp_path / "app",
        ratings_loader=ratings_loader,
        poll_enabled=False,
    )


def _fixture_card_database() -> CardDatabase:
    return build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)


def _draft_state(
    *,
    account_id: str,
    draft_id: str,
    event_name: str,
    pool_grp_ids: tuple[int, ...],
    account_screen_name: str | None = None,
) -> DraftState:
    now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC).isoformat()
    return DraftState(
        account_id=account_id,
        draft_id=draft_id,
        event_name=event_name,
        set_code="MSH",
        course_id=draft_id,
        started_at=now,
        updated_at=now,
        completed_at=now,
        completed=True,
        picks=(),
        pool_grp_ids=pool_grp_ids,
        account_screen_name=account_screen_name,
    )


def _account_lines() -> list[str]:
    return FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()[:2]


def _first_pack_lines() -> list[str]:
    return FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()[:7]


def _first_pick_lines() -> list[str]:
    return FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()[:10]


def _full_fixture_lines() -> list[str]:
    return FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()


def _status_text(*, app: DraftgoblinTuiApp) -> str:
    status = app.query_one("#status-bar", Static)
    return str(status.render())


def _pool_summary_text(*, app: DraftgoblinTuiApp) -> str:
    summary = app.query_one("#pool-summary", Static)
    return str(summary.render())


def _last_picks_text(*, app: DraftgoblinTuiApp) -> str:
    last_picks = app.query_one("#last-picks", Static)
    return str(last_picks.render())


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
