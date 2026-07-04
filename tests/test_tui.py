from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from draftgoblin.carddb import CardDatabase, CardInfo, build_card_database_from_bulk_file
from draftgoblin.pool import DraftPick, DraftState, draft_state_path, save_draft_state
from draftgoblin.seventeen import (
    QUICK_DRAFT_FORMAT,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsFormatData,
)
from draftgoblin.tui import (
    MANA_CARD_TYPE_GLYPHS,
    MANA_ICON_GLYPHS,
    DraftgoblinTuiApp,
    _format_card_colors,
    _format_card_types,
)

FIXTURE_LOG_PATH = Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
FIXTURE_ACCOUNT_ID = "FIXTURECLIENTID1234567890"
FIXTURE_DRAFT_ID = "00000000-0000-4000-8000-000000000004"
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
        assert DraftgoblinTuiApp.TITLE == "Draft Goblin"
        assert any("Fixture Spider" in row for row in _card_cells(rows=rows))
        assert "Account: FixturePlayer" in status
        assert "Pair: open" in status
        assert "Pick: P1P1" in status
        assert "Pool: 0" in status
        assert "Data: neutral prior" in status
        assert "Card data from 17Lands (17lands.com)" in status


def test_tui_pack_rows_show_17lands_win_rate_grade_and_dg_score(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_pack_rows_show_17lands_stats(tmp_path=tmp_path))


async def _assert_pack_rows_show_17lands_stats(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path, ratings_loader=_graded_ratings_data)

    async with app.run_test(size=(140, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        for _ in range(20):
            await pilot.pause(0.05)
            if "MSH" not in app.loading_rating_sets:
                break

        table = app.query_one("#pack-table", DataTable)
        rows = [table.get_row_at(index) for index in range(table.row_count)]
        card_index = app.visible_column_keys.index("card")
        win_rate_index = app.visible_column_keys.index("win_rate")
        grade_index = app.visible_column_keys.index("grade")
        score_index = app.visible_column_keys.index("score")
        split_card_row = next(
            row for row in rows if str(row[card_index]) == "Fixture Split Card"
        )

        assert str(split_card_row[win_rate_index]) == "62.0%"
        assert str(split_card_row[grade_index]) == "B+"
        assert str(split_card_row[score_index]).isdigit()


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
        pool_summary = app.query_one("#pool-summary", Static)
        assert str(title.render()).startswith("Build view unavailable")
        assert "automatic" not in str(title.render())
        assert pool_summary.display is False
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


def test_tui_pack_navigation_preserves_focus_and_updates_details(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_pack_navigation_preserves_focus_and_details(tmp_path=tmp_path))


async def _assert_keybindings_toggle_columns_and_sort(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        assert app.visible_column_keys == (
            "rank",
            "win_rate",
            "grade",
            "score",
            "card",
            "colors",
            "fit",
            "alsa",
            "mv",
            "source",
        )

        await pilot.press("c")
        assert app.visible_column_keys == (
            "rank",
            "win_rate",
            "grade",
            "score",
            "card",
            "colors",
        )

        assert app.sort_mode == "win_rate"
        assert "Ranking: 17L WR" in _status_text(app=app)

        await pilot.press("s")
        assert app.sort_mode == "score"
        assert "Ranking: DG Score" in _status_text(app=app)

        await pilot.press("s")
        assert app.sort_mode == "alsa"
        assert "Ranking: ALSA" in _status_text(app=app)

        await pilot.press("s")
        assert app.sort_mode == "mv"
        assert "Ranking: MV" in _status_text(app=app)

        await pilot.press("q")


async def _assert_pack_navigation_preserves_focus_and_details(
    tmp_path: Path,
) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(140, 30)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        table = app.query_one("#pack-table", DataTable)
        card_index = app.visible_column_keys.index("card")
        second_card_name = str(table.get_row_at(1)[card_index])

        assert app.focused == table
        assert table.cursor_type == "row"
        assert "Focused card details" in _focused_card_text(app=app)

        await pilot.press("down")
        await pilot.pause()

        assert table.cursor_coordinate.row == 1
        assert second_card_name in _focused_card_text(app=app)

        app.process_lines(lines=[])
        await pilot.pause()

        assert app.focused == table
        assert table.cursor_coordinate.row == 1
        assert second_card_name in _focused_card_text(app=app)

        await pilot.press("tab")
        await pilot.pause()

        assert app.focused == table

        await pilot.press("right")
        await pilot.pause()

        assert app.focused == table
        assert table.cursor_coordinate.row == 2

        await pilot.press("left")
        await pilot.pause()

        assert table.cursor_coordinate.row == 1

        await pilot.press("up")
        await pilot.pause()

        assert table.cursor_coordinate.row == 0


def test_tui_sidebar_updates_pool_distribution_and_curve(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_sidebar_updates_pool_summary(tmp_path=tmp_path))


def test_tui_mana_icon_mapping_preserves_plain_fallback() -> None:
    multicolor = CardInfo(
        grp_id=1,
        name="Azorius Fixture",
        colors=("W", "U"),
        mana_value=2.0,
        rarity="common",
        types=("Creature — Wizard",),
    )
    colorless = CardInfo(
        grp_id=2,
        name="Colorless Fixture",
        colors=(),
        mana_value=3.0,
        rarity="common",
        types=("Artifact",),
    )
    unknown = CardInfo.unknown_card(grp_id=3)

    assert _format_card_colors(card=multicolor) == "WU"
    assert _format_card_colors(card=colorless) == "Colorless"
    assert _format_card_colors(card=unknown) == "Unknown"
    assert _format_card_colors(
        card=multicolor,
        mana_icons_enabled=True,
    ) == f"{MANA_ICON_GLYPHS['W']}{MANA_ICON_GLYPHS['U']}"
    assert _format_card_colors(
        card=colorless,
        mana_icons_enabled=True,
    ) == f"{MANA_ICON_GLYPHS['C']} Colorless"
    assert _format_card_colors(
        card=colorless,
        mana_icons_enabled=True,
        long_colorless=False,
    ) == f"{MANA_ICON_GLYPHS['C']} C"
    assert _format_card_colors(card=unknown, mana_icons_enabled=True) == "Unknown"
    assert _format_card_types(
        card=multicolor,
        mana_icons_enabled=True,
    ) == f"{MANA_CARD_TYPE_GLYPHS['Creature']} Creature — Wizard"


def test_tui_mana_icons_toggle_updates_tui_surfaces(tmp_path: Path) -> None:
    asyncio.run(_assert_mana_icons_toggle_updates_surfaces(tmp_path=tmp_path))


async def _assert_sidebar_updates_pool_summary(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(120, 24)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        summary = _pool_summary_text(app=app)

        assert "Pool size: 1" in summary
        assert "Colors:" in summary
        assert "G █████ 1" in summary
        assert "Set: MSH — Marvel Super Heroes" in summary
        assert "Curve:" in summary
        assert "4█1" in summary


async def _assert_mana_icons_toggle_updates_surfaces(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(140, 30)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        table = app.query_one("#pack-table", DataTable)
        rows = [table.get_row_at(index) for index in range(table.row_count)]
        card_index = app.visible_column_keys.index("card")
        colors_index = app.visible_column_keys.index("colors")
        blue_row = next(
            row for row in rows if "Fixture Blue Card" in str(row[card_index])
        )

        assert str(blue_row[colors_index]) == "U"
        assert "Mana icons: off" in _status_text(app=app)

        await pilot.press("m")
        await pilot.pause()

        rows = [table.get_row_at(index) for index in range(table.row_count)]
        blue_row = next(
            row for row in rows if "Fixture Blue Card" in str(row[card_index])
        )
        blue_icon = MANA_ICON_GLYPHS["U"]
        green_icon = MANA_ICON_GLYPHS["G"]

        assert str(blue_row[colors_index]) == blue_icon
        assert f"{green_icon} █████ 1" in _pool_summary_text(app=app)
        assert "Mana icons: on" in _status_text(app=app)

        await pilot.press("b")
        await pilot.pause()

        assert green_icon in app.build_view_text
        assert any(
            MANA_ICON_GLYPHS[color] in _status_text(app=app)
            for color in ("W", "U", "B", "R", "G")
        )

        await pilot.press("c")
        await pilot.pause()

        assert f"01. Fixture Spider | Colors {green_icon} | MV 4" in app.build_view_text


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
        assert app.build_view_text.startswith("[bold]Suggested deck[/bold]\n\n")
        assert "Set: MSH — Marvel Super Heroes" in app.build_view_text
        assert "Pool size: 1 cards" not in app.build_view_text
        assert "Average mana value:" in app.build_view_text
        assert "Mana curve: 0:" not in app.build_view_text
        assert "Selected spells by mana value" in app.build_view_text
        assert "Picked pool" not in app.build_view_text
        assert "Color-pair reasoning" not in app.build_view_text
        assert "Pair scores" not in app.build_view_text
        assert "Details hidden: press c" in app.build_view_text
        assert "Build action: rebuilt current pool" in _status_text(app=app)

        await pilot.press("c")
        await pilot.pause()

        assert "Build context" in app.build_view_text
        assert "Pool size: 1 cards" in app.build_view_text
        assert "Mana curve: 0:" in app.build_view_text
        assert "Picked pool (1)" in app.build_view_text
        assert "01. Fixture Spider | Colors G | MV 4" in app.build_view_text

        await pilot.press("b")
        await pilot.pause()

        assert "Build action: no build needed — current pool already shown" in _status_text(
            app=app,
        )


def test_tui_build_view_collapses_duplicate_selected_spells(tmp_path: Path) -> None:
    asyncio.run(
        _assert_build_view_collapses_duplicate_selected_spells(tmp_path=tmp_path)
    )


async def _assert_build_view_collapses_duplicate_selected_spells(
    tmp_path: Path,
) -> None:
    app = _tui_app(
        tmp_path=tmp_path,
        card_database=CardDatabase(
            cards={
                1: CardInfo(
                    grp_id=1,
                    name="Copy",
                    colors=("G",),
                    mana_value=4.0,
                    rarity="common",
                    types=("Creature",),
                ),
                2: CardInfo(
                    grp_id=2,
                    name="One",
                    colors=("W",),
                    mana_value=2.0,
                    rarity="common",
                    types=("Creature",),
                ),
            }
        ),
    )
    save_draft_state(
        state=_draft_state(
            account_id="FIXTURECLIENTID1234567890",
            draft_id="duplicate-card-draft",
            event_name="QuickDraft_MSH_20260702",
            pool_grp_ids=(1, 1, 1, 2),
        ),
        app_dir=tmp_path / "app",
    )

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_account_lines())
        await pilot.pause()

        text = app.build_view_text
        assert "Selected spells by mana value (4)" in text
        assert "MV 4 (3)" in text
        assert "Copy (G) x3" in text
        assert text.count("Copy (G)") == 1
        assert "One (W)" in text
        assert "One (W) x" not in text
        assert "DG" not in text
        assert "50 Copy (G) x3" in text
        assert "50 One (W)" in text
        assert "Lands: 36" in text
        assert "Basics: 9 Plains, 27 Forest" in text
        assert "Land count:" not in text


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
        pool_summary = app.query_one("#pool-summary", Static)

        assert str(title.render()).startswith("Build view — pair WU (automatic)")
        assert table.display is False
        assert build_scroll.display is True
        assert pool_summary.display is False
        assert app.build_view_text.startswith("[bold]Suggested deck[/bold]\n\n")
        assert "Selected spells by mana value" in app.build_view_text
        assert "Color pair: WU (automatic" in app.build_view_text
        assert "Average mana value:" in app.build_view_text
        assert "Mana curve: 0:" not in app.build_view_text
        assert "▶" in app.build_view_text
        assert "Selected card 1/" in _focused_card_text(app=app)

        await pilot.press("down")
        await pilot.pause()

        assert app.focused == build_scroll
        assert "Selected card 2/" in _focused_card_text(app=app)

        await pilot.press("tab")
        await pilot.pause()

        assert app.focused == build_scroll

        await pilot.press("right")
        await pilot.pause()

        assert app.focused == build_scroll
        assert "Selected card 3/" in _focused_card_text(app=app)

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


def test_tui_backtest_keybinding_opens_recommendation_report(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_backtest_keybinding_opens_report(tmp_path=tmp_path))


async def _assert_backtest_keybinding_opens_report(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(160, 40)) as pilot:
        app.process_lines(lines=_full_fixture_lines())
        await pilot.pause()
        state_path = draft_state_path(
            account_id=FIXTURE_ACCOUNT_ID,
            draft_id=FIXTURE_DRAFT_ID,
            app_dir=tmp_path / "app",
        )
        before = state_path.read_text(encoding="utf-8")

        await pilot.press("t")
        await pilot.pause()

        title = app.query_one("#pack-title", Static)
        table = app.query_one("#pack-table", DataTable)
        build_scroll = app.query_one("#build-scroll", VerticalScroll)
        pool_summary = app.query_one("#pool-summary", Static)

        assert str(title.render()).startswith("Backtest view — 17L WR recommendations")
        assert table.display is False
        assert build_scroll.display is True
        assert pool_summary.display is False
        assert app.focused == build_scroll
        assert app.backtest_view_text.startswith("Draftgoblin backtest\n")
        assert "Ranking: 17L WR" in app.backtest_view_text
        assert "Picks: 42 chosen, 42 compared, 0 skipped" in app.backtest_view_text
        assert "Pack  Pick  Pool  17L WR  DG" in app.backtest_view_text
        assert "Recommended" in app.backtest_view_text
        assert "Actual" in app.backtest_view_text
        assert "Match" in app.backtest_view_text
        assert "Fixture Spider [G] (grpId 105097)" in app.backtest_view_text
        assert "Summary:" in app.backtest_view_text
        assert "View: backtest" in _status_text(app=app)
        assert "Backtest action: rebuilt 17L WR recommendation comparison" in _status_text(
            app=app,
        )
        assert state_path.read_text(encoding="utf-8") == before



def test_tui_backtest_view_reports_missing_history_without_mutating_state(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_backtest_missing_history_is_read_only(tmp_path=tmp_path))


async def _assert_backtest_missing_history_is_read_only(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)
    state = _draft_state(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id="missing-history-draft",
        event_name="QuickDraft_MSH_20260702",
        pool_grp_ids=(105097,),
        picks=(
            DraftPick(
                pack_number=0,
                pick_number=0,
                offered_grp_ids=None,
                pool_before_pick=(),
                chosen_grp_id=105097,
            ),
        ),
    )
    save_draft_state(state=state, app_dir=tmp_path / "app")
    state_path = draft_state_path(
        account_id=state.account_id,
        draft_id=state.draft_id,
        app_dir=tmp_path / "app",
    )
    before = state_path.read_text(encoding="utf-8")

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_account_lines())
        await pilot.pause()

        await pilot.press("t")
        await pilot.pause()

        assert "Picks: 1 chosen, 0 compared, 1 skipped" in app.backtest_view_text
        assert "skipped: missing offered-card history" in app.backtest_view_text
        assert "Summary: no comparable picks; 1 skipped." in app.backtest_view_text
        assert state_path.read_text(encoding="utf-8") == before



def test_tui_card_image_preserves_ratio_with_auto_height(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(
        _assert_card_image_preserves_ratio_with_auto_height(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
    )


async def _assert_card_image_preserves_ratio_with_auto_height(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_sizes: list[tuple[int | None, object]] = []

    class FakeTgpImage:
        def __init__(
            self,
            image: str,
            width: int | None = None,
            height: object = None,
        ) -> None:
            captured_sizes.append((width, height))

        def __rich_console__(self, *args: object) -> list[str]:
            return ["<image>"]

    def successful_fetcher(image_uri: str, cache_dir: Path) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        image_path = cache_dir / "spider.jpg"
        image_path.write_bytes(b"image")
        return image_path

    monkeypatch.setattr("draftgoblin.tui.TgpImage", FakeTgpImage)
    fixture_database = _fixture_card_database()
    app = _tui_app(
        tmp_path=tmp_path,
        card_database=CardDatabase(
            cards=fixture_database.cards,
            image_uris_by_name={"fixture spider": "https://cards.example/spider.jpg"},
        ),
        image_preview_enabled=True,
        card_image_fetcher=successful_fetcher,
    )

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        await pilot.press("b")
        for _ in range(10):
            await pilot.pause(0.05)
            if captured_sizes:
                break

        assert captured_sizes
        width, height = captured_sizes[-1]
        assert width is not None and width <= 30
        assert height == "auto"


def test_tui_build_focused_card_image_fetch_failure_stays_nonblocking(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_build_image_fetch_failure_stays_nonblocking(tmp_path=tmp_path))


async def _assert_build_image_fetch_failure_stays_nonblocking(tmp_path: Path) -> None:
    started = threading.Event()

    def failing_fetcher(image_uri: str, cache_dir: Path) -> Path:
        assert image_uri == "https://cards.example/spider.jpg"
        started.set()
        raise OSError("network down")

    fixture_database = _fixture_card_database()
    app = _tui_app(
        tmp_path=tmp_path,
        card_database=CardDatabase(
            cards=fixture_database.cards,
            image_uris_by_name={"fixture spider": "https://cards.example/spider.jpg"},
        ),
        image_preview_enabled=True,
        card_image_fetcher=failing_fetcher,
    )

    async with app.run_test(size=(140, 40)) as pilot:
        app.process_lines(lines=_first_pick_lines())
        await pilot.pause()

        await pilot.press("b")
        await pilot.pause()

        assert "Selected card 1/" in _focused_card_text(app=app)
        assert await asyncio.to_thread(started.wait, 0.5)
        for _ in range(10):
            await pilot.pause(0.05)
            image_text = _card_image_preview_text(app=app)
            if "Image preview unavailable" in image_text:
                break

        image_panel = app.query_one("#card-image-preview", Static)
        assert image_panel.display is True
        assert "Image preview unavailable" in _card_image_preview_text(app=app)
        assert "network down" in _card_image_preview_text(app=app)
        assert "Error:" not in _status_text(app=app)
        assert app.build_view_text.startswith("[bold]Suggested deck[/bold]\n\n")


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
        assert "Draft: recovered-draft" not in app.build_view_text
        assert "Pool size: 2 cards" not in app.build_view_text

        await pilot.press("c")
        await pilot.pause()

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
        assert "Draft: test-draft" not in app.build_view_text
        assert "Pool size: 2 cards" not in app.build_view_text

        await pilot.press("c")
        await pilot.pause()

        assert "Draft: test-draft" in app.build_view_text
        assert "Pool size: 2 cards" in app.build_view_text


def test_tui_narrow_width_hides_secondary_columns_first(tmp_path: Path) -> None:
    asyncio.run(_assert_narrow_width_hides_secondary_columns(tmp_path=tmp_path))


async def _assert_narrow_width_hides_secondary_columns(tmp_path: Path) -> None:
    app = _tui_app(tmp_path=tmp_path)

    async with app.run_test(size=(60, 24)) as pilot:
        app.process_lines(lines=_first_pack_lines())
        await pilot.pause()

        assert app.visible_column_keys == (
            "rank",
            "win_rate",
            "grade",
            "score",
            "card",
            "colors",
        )


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
            assert app.sort_mode == "score"

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
    image_preview_enabled: bool | None = None,
    mana_icons_enabled: bool = False,
    card_image_fetcher: Callable[[str, Path], Path] | None = None,
) -> DraftgoblinTuiApp:
    return DraftgoblinTuiApp(
        log_path=tmp_path / "Player.log",
        card_database=(
            card_database if card_database is not None else _fixture_card_database()
        ),
        app_dir=tmp_path / "app",
        ratings_loader=ratings_loader,
        poll_enabled=False,
        image_preview_enabled=image_preview_enabled,
        mana_icons_enabled=mana_icons_enabled,
        card_image_fetcher=card_image_fetcher,
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
    picks: tuple[DraftPick, ...] = (),
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
        picks=picks,
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


def _focused_card_text(*, app: DraftgoblinTuiApp) -> str:
    focused_card = app.query_one("#focused-card", Static)
    return str(focused_card.render())


def _card_image_preview_text(*, app: DraftgoblinTuiApp) -> str:
    preview = app.query_one("#card-image-preview", Static)
    return str(preview.render())


def _card_cells(*, rows: list[list[object]]) -> list[str]:
    return [str(row[4]) for row in rows]


def _graded_ratings_data(set_code: str) -> SeventeenLandsData:
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
            105097: _stats(
                grp_id=105097,
                name="Fixture Spider",
                color="G",
                gih=0.60,
                games_in_hand=900,
                alsa=2.1,
            ),
            104976: _stats(
                grp_id=104976,
                name="Fixture Red Card",
                color="R",
                gih=0.56,
                games_in_hand=900,
                alsa=4.0,
            ),
            105080: _stats(
                grp_id=105080,
                name="Fixture Black Card",
                color="B",
                gih=0.54,
                games_in_hand=900,
                alsa=5.0,
            ),
            104995: _stats(
                grp_id=104995,
                name="Fixture Filler Card",
                color="C",
                gih=0.52,
                games_in_hand=900,
                alsa=6.0,
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
