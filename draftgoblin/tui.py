"""Textual live interface for Draftgoblin watch mode.
Render ranked packs and status updates without blocking fetches.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from os import PathLike
from pathlib import Path
from typing import TypeAlias

from rich.align import Align
from rich.console import Group
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Header, Static
from textual.worker import get_current_worker

try:  # pragma: no cover - import availability depends on optional terminal extras.
    from textual_image.renderable.tgp import Image as TgpImage
except Exception:  # pragma: no cover - graceful fallback when unavailable.
    TgpImage = None

from draftgoblin.backtest import format_backtest_report, generate_backtest_report
from draftgoblin.carddb import SCRYFALL_USER_AGENT, CardDatabase, CardInfo
from draftgoblin.config import COLOR_PAIRS, POLL_INTERVAL_SECONDS
from draftgoblin.deckbuilder import (
    BuildPool,
    DeckBuilderError,
    ManaBase,
    PairSelection,
    SpellSelection,
    build_deck_from_pool,
)
from draftgoblin.events import (
    EXPECTED_PICKS_PER_PACK,
    AccountEvent,
    DraftCompletedEvent,
    DraftEvent,
    DraftLogParser,
    DraftStartedEvent,
    PackOfferedEvent,
    PickMadeEvent,
)
from draftgoblin.logfollow import LogFollower
from draftgoblin.pickengine import PickEngine, ScoredCard, ScoredPack
from draftgoblin.pool import DraftPoolError, DraftPoolStore, DraftState, list_draft_states
from draftgoblin.ranking import (
    DEFAULT_RANKING_MODE,
    RANKING_LABELS,
    RANKING_MODES,
    rank_scored_cards,
    ranking_label,
)
from draftgoblin.seventeen import SEVENTEEN_LANDS_ATTRIBUTION, SeventeenLandsData
from draftgoblin.setinfo import format_set_label

PathInput: TypeAlias = str | PathLike[str]
RatingsLoader: TypeAlias = Callable[[str], SeventeenLandsData]
CardImageFetcher: TypeAlias = Callable[[str, Path], Path]
BuildSignature: TypeAlias = tuple[str, tuple[int, ...], str | None]
TuiCardQuantityKey: TypeAlias = tuple[str, str]
TuiCardQuantityGroup: TypeAlias = tuple[ScoredCard, int]

PRIMARY_COLUMN_KEYS = ("rank", "win_rate", "grade", "score", "card", "colors")
SECONDARY_COLUMN_KEYS = ("fit", "alsa", "mv", "source")
SORT_MODES = RANKING_MODES
BUILD_SPELL_SORT_MODES = ("curve", "score", "name")
SECONDARY_COLUMN_MIN_WIDTH = 88
SIDEBAR_MIN_WIDTH = 56
COLOR_ORDER = ("W", "U", "B", "R", "G")
COLORLESS_KEY = "C"
UNKNOWN_COLOR_KEY = "?"
CURVE_BUCKET_LABELS = ("0", "1", "2", "3", "4", "5", "6+")
SPARKLINE_GLYPHS = "▁▂▃▄▅▆▇█"
CARD_IMAGE_CACHE_DIR_NAME = "card-images"
CARD_IMAGE_PREVIEW_MAX_BYTES = 8 * 1024 * 1024
CARD_IMAGE_PREVIEW_TIMEOUT_SECONDS = 10
CARD_IMAGE_PREVIEW_ENV = "DRAFTGOBLIN_CARD_IMAGES"
CARD_IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CLOSE_DG_SCORE_THRESHOLD = 3.0
CLOSE_WIN_RATE_THRESHOLD = 0.01
MANA_ICON_GLYPHS = {
    "W": "\ue600",
    "U": "\ue601",
    "B": "\ue602",
    "R": "\ue603",
    "G": "\ue604",
    "C": "\ue904",
}
MANA_CARD_TYPE_GLYPHS = {
    "Artifact": "\ue61e",
    "Creature": "\ue61f",
    "Enchantment": "\ue620",
    "Instant": "\ue621",
    "Land": "\ue622",
    "Planeswalker": "\ue623",
    "Sorcery": "\ue624",
}

COLOR_STYLES = {
    "W": "bold bright_white",
    "U": "bold dodger_blue1",
    "B": "bold grey50",
    "R": "bold red3",
    "G": "bold green3",
}

COLUMN_LABELS = {
    "rank": "#",
    "win_rate": "17L WR",
    "grade": "17L Grade",
    "score": "DG",
    "card": "Card",
    "colors": "Colors",
    "fit": "Fit",
    "gih": "GIH WR",
    "alsa": "ALSA",
    "mv": "MV",
    "source": "Source",
}

COLUMN_WIDTHS = {
    "rank": 3,
    "win_rate": 8,
    "grade": 9,
    "score": 5,
    "card": None,
    "colors": 10,
    "fit": 6,
    "gih": 8,
    "alsa": 7,
    "mv": 5,
    "source": 9,
}

SORT_LABELS = RANKING_LABELS


class CardDetailsPanel(Static, can_focus=False):
    """Sidebar panel for the highlighted card.
    Keep focus styling ready for future actions, but do not enter it with Tab yet.
    """


class DraftgoblinTuiApp(App[None]):
    """Textual app for live Quick Draft recommendations.
    The app can tail a real log or accept fixture lines in tests.
    """

    TITLE = "Draft Goblin"

    CSS = """
    Screen {
        layout: vertical;
    }

    #main {
        height: 1fr;
    }

    #pack-panel {
        width: 3fr;
        min-width: 0;
        padding: 0 1;
    }

    #sidebar {
        width: 1fr;
        min-width: 24;
        padding: 0 1;
        border-left: solid $accent;
    }

    #pack-title {
        height: 1;
        text-style: bold;
    }

    #pack-table,
    #build-scroll,
    #focused-card {
        border: blank $surface;
    }

    #pack-table:focus,
    #build-scroll:focus,
    #focused-card:focus {
        border: solid $accent;
    }

    #pack-table {
        height: 1fr;
    }

    #build-scroll {
        height: 1fr;
        overflow-y: auto;
    }

    #build-view {
        height: auto;
    }

    #pool-summary,
    #focused-card,
    #card-image-preview {
        height: auto;
        margin-bottom: 1;
    }

    #card-image-preview {
        content-align: center top;
        margin-top: 1;
        text-align: center;
    }

    #status-bar {
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("c", "toggle_secondary_columns", "Columns", show=True),
        Binding("s", "cycle_sort", "Rank", show=True),
        Binding("b", "open_build_view", "Build", show=True),
        Binding("t", "open_backtest_report", "Backtest", show=True),
        Binding("a", "cycle_account", "Account", show=True),
        Binding("p", "rebuild_with_pair_override", "Pair", show=True),
        Binding("m", "toggle_mana_icons", "Mana", show=True),
        Binding("up", "navigate_previous_card", "Previous", show=False, priority=True),
        Binding("left", "navigate_previous_card", "Previous", show=False, priority=True),
        Binding("k", "navigate_previous_card", "Previous", show=False, priority=True),
        Binding("down", "navigate_next_card", "Next", show=False, priority=True),
        Binding("right", "navigate_next_card", "Next", show=False, priority=True),
        Binding("j", "navigate_next_card", "Next", show=False, priority=True),
        Binding("pageup", "navigate_page_up", "Page", show=False, priority=True),
        Binding("pagedown", "navigate_page_down", "Page", show=False, priority=True),
        Binding("home", "navigate_home", "Home", show=False, priority=True),
        Binding("end", "navigate_end", "End", show=False, priority=True),
    ]

    def __init__(
        self,
        *,
        log_path: PathInput,
        card_database: CardDatabase,
        app_dir: PathInput | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        previous_log_path: PathInput | None = None,
        ratings_loader: RatingsLoader | None = None,
        startup_scan: bool = False,
        once: bool = False,
        poll_enabled: bool = True,
        image_preview_enabled: bool | None = None,
        mana_icons_enabled: bool = False,
        card_image_fetcher: CardImageFetcher | None = None,
    ) -> None:
        super().__init__()
        self.log_path = Path(log_path).expanduser().resolve(strict=False)
        self.card_database = card_database
        self.follower = LogFollower(
            log_path=self.log_path,
            app_dir=app_dir,
            poll_interval=poll_interval,
            previous_log_path=previous_log_path,
        )
        self.parser = DraftLogParser()
        self._login_generation = self.parser.login_generation
        self.store = DraftPoolStore(app_dir=app_dir)
        self.ratings_loader = ratings_loader
        self.startup_scan = startup_scan
        self.once = once
        self.poll_enabled = poll_enabled
        self.poll_interval = poll_interval
        self.card_image_fetcher = card_image_fetcher or _fetch_card_image
        self._card_image_cache_dir = _card_image_cache_dir(app_dir=app_dir)
        self._card_image_preview_enabled = (
            _card_image_preview_enabled(env=os.environ)
            if image_preview_enabled is None
            else image_preview_enabled
        )
        self.mana_icons_enabled = mana_icons_enabled
        self._card_image_paths_by_uri: dict[str, Path] = {}
        self._card_image_failures_by_uri: dict[str, str] = {}
        self._loading_card_image_uris: set[str] = set()
        self._card_image_uris_by_grp_id: dict[int, str] = {}

        self.show_secondary_columns = True
        self.sort_mode = DEFAULT_RANKING_MODE
        self._view_mode = "pack"
        self._visible_column_keys: tuple[str, ...] = ()
        self._account_labels: dict[str, str] = {}
        self._log_account_id: str | None = None
        self._active_account_id: str | None = None
        self._active_account_label = "unknown"
        self._event_name: str | None = None
        self._set_code: str | None = None
        self._draft_id: str | None = None
        self._pick_label = "—"
        self._pool_size = 0
        self._pool_grp_ids: tuple[int, ...] = ()
        self._pair_label = "open"
        self._commitment_label = "0% open"
        self._data_source = "unknown"
        self._last_error: str | None = None
        self._last_picks: list[str] = []
        self._forced_pair: str | None = None
        self._build_pair_label = "—"
        self._build_text = "Build view: no picked cards yet."
        self._build_error: str | None = None
        self._build_action_status: str | None = None
        self._backtest_text = "Backtest view: complete a draft, then press t."
        self._backtest_error: str | None = None
        self._backtest_action_status: str | None = None
        self._last_build_signature: BuildSignature | None = None
        self._build_spell_sort_mode = "curve"
        self._build_show_details = False
        self._build_focus_cards: tuple[TuiCardQuantityGroup, ...] = ()
        self._build_focused_card_index = 0
        self._build_render_pool: BuildPool | None = None
        self._build_render_selection: PairSelection | None = None
        self._build_render_spell_selection: SpellSelection | None = None
        self._build_render_mana_base: ManaBase | None = None
        self._current_pack_event: PackOfferedEvent | None = None
        self._current_pack: ScoredPack | None = None
        self._ratings_data_by_set: dict[str, SeventeenLandsData | None] = {}
        self._rating_errors_by_set: dict[str, str] = {}
        self._loading_rating_sets: set[str] = set()
        self._draft_states_by_key: dict[tuple[str, str], DraftState] = {}

    @property
    def visible_column_keys(self) -> tuple[str, ...]:
        """Return current pack-table columns for tests.
        The value reflects width-based degradation and user toggles.
        """

        return self._visible_column_keys

    @property
    def loading_rating_sets(self) -> frozenset[str]:
        """Return set codes currently refreshing in a worker.
        Tests use this to confirm slow loads stay off the render loop.
        """

        return frozenset(self._loading_rating_sets)

    @property
    def build_view_text(self) -> str:
        """Return the rendered build view text for pilot tests.
        This mirrors the Static widget content after a build refresh.
        """

        return self._build_text

    @property
    def backtest_view_text(self) -> str:
        """Return the rendered backtest report text for pilot tests.
        This mirrors the Static widget content after a report refresh.
        """

        return self._backtest_text

    def compose(self) -> ComposeResult:
        """Compose the pack table, sidebar, status bar, and key footer.
        Textual's Footer automatically renders the declared keybindings.
        """

        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="pack-panel"):
                yield Static("Waiting for a Quick Draft pack…", id="pack-title")
                yield DataTable(id="pack-table")
                with VerticalScroll(id="build-scroll", can_focus=True):
                    yield Static("Build view: no picked cards yet.", id="build-view")
            with Vertical(id="sidebar"):
                yield Static("Pool: no draft yet", id="pool-summary")
                yield Static("", id="card-image-preview")
                yield CardDetailsPanel("Focused card: none", id="focused-card")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Prepare widgets and start live log polling.
        The first poll is scheduled as a worker so file I/O does not block UI.
        """

        table = self.query_one("#pack-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.focus()
        self._render_all()

        if not self.poll_enabled:
            return

        if self.startup_scan:
            self._scan_startup_files_worker(exit_after=self.once)
        else:
            self._poll_log_worker(exit_after=self.once)

        if not self.once:
            self.set_interval(self.poll_interval, self._poll_log_worker)

    def on_resize(self, event: events.Resize) -> None:
        """Rebuild the table when width crosses a degradation threshold.
        Secondary columns are hidden first on narrow terminals.
        """

        self._render_all()

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        """Refresh card details when keyboard navigation changes rows.
        Periodic log polls should not be needed before details update.
        """

        if event.data_table.id == "pack-table":
            self._render_focused_card_details()

    def action_toggle_secondary_columns(self) -> None:
        """Toggle secondary pack columns or build detail sections.
        In build view this makes the key produce an immediate visible change.
        """

        if self._view_mode == "build":
            self._build_show_details = not self._build_show_details
            self._rebuild_build_view()
        else:
            self.show_secondary_columns = not self.show_secondary_columns

        self._render_all()

    def action_toggle_mana_icons(self) -> None:
        """Toggle opt-in Mana font icon rendering.
        Terminals need Andrew Gioia's Mana font installed before enabling it.
        """

        self.mana_icons_enabled = not self.mana_icons_enabled
        if self._view_mode == "build":
            self._rebuild_build_view()

        self._render_all()

    def action_cycle_sort(self) -> None:
        """Cycle pack ranking or build spell grouping.
        Backtest reports are rebuilt with the selected recommendation ranking.
        """

        if self._view_mode == "build":
            index = BUILD_SPELL_SORT_MODES.index(self._build_spell_sort_mode)
            self._build_spell_sort_mode = BUILD_SPELL_SORT_MODES[
                (index + 1) % len(BUILD_SPELL_SORT_MODES)
            ]
            self._rebuild_build_view()
        else:
            index = SORT_MODES.index(self.sort_mode)
            self.sort_mode = SORT_MODES[(index + 1) % len(SORT_MODES)]
            if self._view_mode == "backtest":
                self._rebuild_backtest_view()
                self._backtest_action_status = (
                    f"rebuilt {ranking_label(ranking_mode=self.sort_mode)} "
                    "recommendation comparison"
                )

        self._render_all()

    def action_open_build_view(self) -> None:
        """Build the current pool and show the build sheet.
        The draft may still be in progress when this is invoked.
        """

        pool = self._current_build_pool()
        if pool is not None:
            signature = self._build_signature(pool=pool)
            if (
                self._view_mode == "build"
                and self._build_error is None
                and self._last_build_signature == signature
            ):
                self.query_one("#build-scroll", VerticalScroll).scroll_home(animate=False)
                self._last_error = None
                self._build_action_status = "no build needed — current pool already shown"
                self._render_all()
                return

        if self._rebuild_build_view():
            self._view_mode = "build"
        self._record_build_action_result(success_message="rebuilt current pool")
        self._render_all()

    def action_open_backtest_report(self) -> None:
        """Run a saved-pick dry run and show recommendation matches.
        The report uses persisted offered-card and pre-pick pool history.
        """

        self._build_action_status = None
        if self._rebuild_backtest_view():
            self._view_mode = "backtest"
            self.query_one("#build-scroll", VerticalScroll).scroll_home(animate=False)
            self._last_error = None
            self._backtest_action_status = (
                f"rebuilt {ranking_label(ranking_mode=self.sort_mode)} "
                "recommendation comparison"
            )
        else:
            self._backtest_action_status = f"cannot backtest — {self._backtest_error}"

        self._render_all()

    def action_navigate_previous_card(self) -> None:
        """Move to the previous card in the active card list.
        Left, Up, and k share this action for predictable keyboard browsing.
        """

        if self._view_mode == "build":
            self._move_build_card_cursor(delta=-1)
            return

        if self._view_mode == "backtest":
            self.query_one("#build-scroll", VerticalScroll).scroll_page_up(animate=False)
            return

        self._move_pack_cursor(delta=-1)

    def action_navigate_next_card(self) -> None:
        """Move to the next card in the active card list.
        Right, Down, and j share this action for predictable keyboard browsing.
        """

        if self._view_mode == "build":
            self._move_build_card_cursor(delta=1)
            return

        if self._view_mode == "backtest":
            self.query_one("#build-scroll", VerticalScroll).scroll_page_down(animate=False)
            return

        self._move_pack_cursor(delta=1)

    def action_navigate_page_up(self) -> None:
        """Page up in the current keyboard-navigation context.
        Pack view moves the card cursor; build view scrolls the deck sheet.
        """

        if self._view_mode in {"build", "backtest"}:
            self.query_one("#build-scroll", VerticalScroll).scroll_page_up(animate=False)
            return

        self._move_pack_cursor(delta=-self._pack_cursor_page_size())

    def action_navigate_page_down(self) -> None:
        """Page down in the current keyboard-navigation context.
        Pack view moves the card cursor; build view scrolls the deck sheet.
        """

        if self._view_mode in {"build", "backtest"}:
            self.query_one("#build-scroll", VerticalScroll).scroll_page_down(animate=False)
            return

        self._move_pack_cursor(delta=self._pack_cursor_page_size())

    def action_navigate_home(self) -> None:
        """Jump to the first pack card or the top of the build view.
        This keeps Home useful in both major TUI modes.
        """

        if self._view_mode in {"build", "backtest"}:
            self.query_one("#build-scroll", VerticalScroll).scroll_home(animate=False)
            return

        self._move_pack_cursor_to(row=0)

    def action_navigate_end(self) -> None:
        """Jump to the last pack card or the bottom of the build view.
        This keeps End useful in both major TUI modes.
        """

        if self._view_mode in {"build", "backtest"}:
            self.query_one("#build-scroll", VerticalScroll).scroll_end(animate=False)
            return

        table = self.query_one("#pack-table", DataTable)
        self._move_pack_cursor_to(row=table.row_count - 1)

    def action_cycle_account(self) -> None:
        """Cycle known accounts and use the latest recovered draft when available.
        Multiple saved drafts for one account must not create duplicate stops.
        """

        states = self._available_account_draft_states()
        state_by_account_id = {state.account_id: state for state in states}
        account_ids = set(state_by_account_id) | set(self._account_labels)
        if self._log_account_id is not None:
            account_ids.add(self._log_account_id)
        if self._active_account_id is not None:
            account_ids.add(self._active_account_id)
        if not account_ids:
            self._record_error("no known accounts to switch to")
            return

        ordered_account_ids = tuple(
            sorted(
                account_ids,
                key=lambda account_id: (
                    self._account_label(account_id=account_id),
                    account_id,
                ),
            )
        )
        if self._active_account_id in ordered_account_ids:
            index = (ordered_account_ids.index(self._active_account_id) + 1) % len(
                ordered_account_ids
            )
        else:
            index = 0

        account_id = ordered_account_ids[index]
        state = state_by_account_id.get(account_id)
        if state is None:
            self._select_account_without_draft(account_id=account_id)
        else:
            self._select_draft_state(state=state)
        self._render_all()

    def action_rebuild_with_pair_override(self) -> None:
        """Force the next color pair and refresh the build view.
        Repeated presses cycle through all configured color pairs.
        """

        self._forced_pair = self._next_forced_pair()
        if self._rebuild_build_view():
            self._view_mode = "build"
        self._record_build_action_result(
            success_message=f"rebuilt with forced pair {self._forced_pair}",
        )
        self._render_all()

    def process_lines(self, *, lines: Iterable[str]) -> None:
        """Process complete Player.log lines and refresh the TUI.
        Tests call this directly to simulate a live fixture stream.
        """

        try:
            for line in lines:
                events_tuple = tuple(self.parser.parse_lines(lines=(line,)))
                self._discard_previous_login_account_context()
                for parsed_event in events_tuple:
                    event = self._event_with_active_account(event=parsed_event)
                    state = self._consume_store_event(event=event)
                    if state is not None:
                        self._remember_draft_state(state=state)
                        if _event_is_missing_account(event=event):
                            event = replace(event, account_id=state.account_id)
                    self._consume_event(event=event, state=state)
            self._persist_pending_login_name_for_observed_course()
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self._last_error = str(error)

        self._render_all()

    @work(thread=True, exclusive=True, group="log-poll")
    def _poll_log_worker(self, *, exit_after: bool = False) -> None:
        """Poll the followed log in a worker thread.
        Parsed lines are handed back to Textual on the main thread.
        """

        try:
            lines = tuple(self.follower.poll())
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.call_from_thread(self._record_error, str(error))
            if exit_after:
                self.call_from_thread(self.exit)
            return

        self.call_from_thread(self.process_lines, lines=lines)
        if exit_after:
            self.call_from_thread(self.exit)

    @work(thread=True, exclusive=True, group="startup-scan")
    def _scan_startup_files_worker(self, *, exit_after: bool = False) -> None:
        """Scan startup recovery files in a worker thread.
        Startup scans may read Player-prev.log and the current Player.log.
        """

        try:
            lines = tuple(self.follower.scan_startup_files(include_previous=True))
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.call_from_thread(self._record_error, str(error))
            if exit_after:
                self.call_from_thread(self.exit)
            return

        self.call_from_thread(self.process_lines, lines=lines)
        if exit_after:
            self.call_from_thread(self.exit)

    @work(thread=True, group="ratings")
    def _load_ratings_worker(self, set_code: str) -> None:
        """Load or refresh 17Lands ratings away from the render loop.
        UI updates are marshalled back to Textual's main thread.
        """

        worker = get_current_worker()
        if worker.is_cancelled:
            return

        ratings_data = None
        error_message = None
        try:
            if self.ratings_loader is not None:
                ratings_data = self.ratings_loader(set_code)
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            error_message = str(error)

        if worker.is_cancelled:
            return

        self.call_from_thread(
            self._finish_ratings_load,
            set_code,
            ratings_data,
            error_message,
        )

    def _discard_previous_login_account_context(self) -> None:
        if self.parser.login_generation == self._login_generation:
            return

        self._login_generation = self.parser.login_generation
        self._log_account_id = None
        self.store.clear_active_account()

    def _consume_store_event(self, *, event: DraftEvent) -> DraftState | None:
        try:
            return self.store.consume(event=event)
        except DraftPoolError as error:
            if _is_missing_account_error(event=event, error=error):
                return None

            raise

    def _event_with_active_account(self, *, event: DraftEvent) -> DraftEvent:
        if self._log_account_id is None:
            return event

        if not _event_is_missing_account(event=event):
            return event

        return replace(event, account_id=self._log_account_id)

    def _consume_event(self, *, event: DraftEvent, state: DraftState | None) -> None:
        if isinstance(event, AccountEvent):
            self._consume_account_event(event=event)
            return

        if isinstance(event, DraftStartedEvent):
            self._consume_started_event(event=event)
            return

        if isinstance(event, PackOfferedEvent):
            self._consume_pack_event(event=event)
            return

        if isinstance(event, PickMadeEvent):
            self._consume_pick_event(event=event, state=state)
            return

        if isinstance(event, DraftCompletedEvent):
            self._consume_completed_event(event=event, state=state)

    def _consume_account_event(self, *, event: AccountEvent) -> None:
        self._log_account_id = event.client_id
        self._remember_account_label_for(
            client_id=event.client_id,
            screen_name=event.screen_name,
            replace=True,
        )
        if self._active_account_id == event.client_id:
            self._active_account_label = self._account_label(account_id=event.client_id)
        elif self._should_display_account_event(event=event):
            self._active_account_id = event.client_id
            self._active_account_label = self._account_label(account_id=event.client_id)
            self._recover_latest_account_state(account_id=event.client_id)

    def _should_display_account_event(self, *, event: AccountEvent) -> bool:
        return (
            self._active_account_id in {None, event.client_id}
            and self._draft_id is None
            and self._current_pack_event is None
            and not self._pool_grp_ids
        )

    def _recover_latest_account_state(self, *, account_id: str) -> None:
        if (
            self._draft_id is not None
            or self._current_pack_event is not None
            or self._pool_grp_ids
        ):
            return

        states = tuple(
            state
            for state in self._available_draft_states()
            if state.account_id == account_id and state.pool_grp_ids
        )
        if not states:
            return

        self._select_draft_state(state=max(states, key=_latest_draft_state_sort_key))

    def _consume_started_event(self, *, event: DraftStartedEvent) -> None:
        self._active_account_id = self._display_account_id(account_id=event.account_id)
        self._active_account_label = self._account_label(
            account_id=self._active_account_id
        )
        self._event_name = event.event_name
        self._set_code = event.set_code
        self._draft_id = event.course_id
        self._pick_label = "waiting"
        self._pool_size = 0
        self._pool_grp_ids = ()
        self._last_picks = []
        self._current_pack_event = None
        self._current_pack = None
        self._pair_label = "open"
        self._commitment_label = "0% open"
        self._view_mode = "pack"
        self._forced_pair = None
        self._build_pair_label = "—"
        self._build_text = "Build view: no picked cards yet."
        self._build_error = None
        self._build_action_status = None
        self._backtest_text = "Backtest view: complete a draft, then press t."
        self._backtest_error = None
        self._backtest_action_status = None
        self._last_build_signature = None
        self._build_spell_sort_mode = "curve"
        self._build_show_details = False
        self._clear_build_render_state()
        self._ensure_ratings_load_started(set_code=event.set_code)

    def _consume_pack_event(self, *, event: PackOfferedEvent) -> None:
        self._active_account_id = self._display_account_id(account_id=event.account_id)
        self._active_account_label = self._account_label(
            account_id=self._active_account_id
        )
        self._event_name = event.event_name
        self._set_code = event.set_code
        self._pick_label = f"P{event.pack_number + 1}P{event.pick_number + 1}"
        self._pool_size = len(event.pool_grp_ids)
        self._pool_grp_ids = event.pool_grp_ids
        self._build_action_status = None
        self._backtest_action_status = None
        self._current_pack_event = event
        self._view_mode = "pack"
        self._ensure_ratings_load_started(set_code=event.set_code)
        self._score_current_pack()

    def _consume_pick_event(
        self,
        *,
        event: PickMadeEvent,
        state: DraftState | None,
    ) -> None:
        self._active_account_id = self._display_account_id(account_id=event.account_id)
        self._active_account_label = self._account_label(
            account_id=self._active_account_id
        )
        self._pick_label = f"P{event.pack_number + 1}P{event.pick_number + 1} picked"
        if state is not None:
            self._pool_size = len(state.pool_grp_ids)
            self._pool_grp_ids = state.pool_grp_ids
        else:
            self._pool_grp_ids = self._pool_grp_ids + (event.chosen_grp_id,)
            self._pool_size = len(self._pool_grp_ids)

        self._build_action_status = None
        self._backtest_action_status = None
        card = self.card_database.lookup(grp_id=event.chosen_grp_id)
        self._last_picks.append(_format_card_name(card=card))
        self._last_picks = self._last_picks[-5:]

    def _consume_completed_event(
        self,
        *,
        event: DraftCompletedEvent,
        state: DraftState | None,
    ) -> None:
        self._active_account_id = self._display_account_id(account_id=event.account_id)
        self._active_account_label = self._account_label(
            account_id=self._active_account_id
        )
        self._event_name = event.event_name
        self._set_code = event.set_code
        self._pick_label = "complete"
        self._pool_grp_ids = event.picked_grp_ids if state is None else state.pool_grp_ids
        self._pool_size = len(self._pool_grp_ids)
        self._build_action_status = None
        self._backtest_action_status = None
        self._current_pack_event = None
        self._current_pack = None
        self._ensure_ratings_load_started(set_code=event.set_code)
        if self._rebuild_build_view():
            self._view_mode = "build"

    def _ensure_ratings_load_started(self, *, set_code: str) -> None:
        if self.ratings_loader is None:
            return

        if set_code in self._ratings_data_by_set or set_code in self._loading_rating_sets:
            return

        self._loading_rating_sets.add(set_code)
        if self.is_running:
            self._load_ratings_worker(set_code)

    def _finish_ratings_load(
        self,
        set_code: str,
        ratings_data: SeventeenLandsData | None,
        error_message: str | None,
    ) -> None:
        self._loading_rating_sets.discard(set_code)
        self._ratings_data_by_set[set_code] = ratings_data
        if error_message is None:
            self._rating_errors_by_set.pop(set_code, None)
        else:
            self._rating_errors_by_set[set_code] = error_message

        if (
            self._current_pack_event is not None
            and self._current_pack_event.set_code == set_code
        ):
            self._score_current_pack()

        if self._view_mode == "build" and self._set_code == set_code:
            self._rebuild_build_view()

        if self._view_mode == "backtest" and self._set_code == set_code:
            self._rebuild_backtest_view()

        self._render_all()

    def _remember_draft_state(self, *, state: DraftState) -> None:
        self._draft_states_by_key[(state.account_id, state.draft_id)] = state
        self._remember_account_label(state=state)

    def _remember_account_label(self, *, state: DraftState) -> None:
        self._remember_account_label_for(
            client_id=state.account_id,
            screen_name=state.account_screen_name,
            replace=False,
        )

    def _remember_account_label_for(
        self,
        *,
        client_id: str,
        screen_name: str | None,
        replace: bool,
    ) -> None:
        if screen_name is None:
            return

        existing_label = self._account_labels.get(client_id)
        if replace or existing_label is None or existing_label == client_id:
            self._account_labels[client_id] = _format_account_label(
                client_id=client_id,
                screen_name=screen_name,
            )

    def _recovered_draft_states(self) -> tuple[DraftState, ...]:
        states = dict(self._draft_states_by_key)
        states.update(
            {
                (state.account_id, state.draft_id): state
                for state in list_draft_states(app_dir=self.store.app_dir)
            }
        )
        values = tuple(states.values())
        for state in values:
            self._remember_account_label(state=state)

        return values

    def _available_draft_states(self) -> tuple[DraftState, ...]:
        values = self._recovered_draft_states()
        if not values:
            return ()

        matching_event = tuple(
            state
            for state in values
            if self._event_name is not None
            and state.event_name == self._event_name
            and state.set_code == self._set_code
        )
        if len(matching_event) > 1:
            return tuple(sorted(matching_event, key=self._draft_state_sort_key))

        matching_set = tuple(
            state
            for state in values
            if self._set_code is not None and state.set_code == self._set_code
        )
        if len(matching_set) > 1:
            return tuple(sorted(matching_set, key=self._draft_state_sort_key))

        return tuple(sorted(values, key=self._draft_state_sort_key))

    def _available_account_draft_states(self) -> tuple[DraftState, ...]:
        latest_state_by_account: dict[str, DraftState] = {}
        for state in self._recovered_draft_states():
            current = latest_state_by_account.get(state.account_id)
            if current is None or _latest_draft_state_sort_key(state) > _latest_draft_state_sort_key(
                current
            ):
                latest_state_by_account[state.account_id] = state

        return tuple(
            sorted(
                latest_state_by_account.values(),
                key=self._draft_state_sort_key,
            )
        )

    def _draft_state_sort_key(self, state: DraftState) -> tuple[str, str, str, str]:
        return (
            self._account_label(account_id=state.account_id),
            state.set_code,
            state.event_name,
            state.draft_id,
        )

    def _persist_pending_login_name_for_observed_course(self) -> None:
        """Persist an unbound login name when one saved account owns its course.
        Arena can omit authentication while still listing that account's active draft.
        """

        screen_name = self.parser.pending_login_screen_name
        course_ids = self.parser.observed_quick_draft_course_ids
        if screen_name is None or not course_ids:
            return

        states_by_key = dict(self._draft_states_by_key)
        states_by_key.update(
            {
                (candidate.account_id, candidate.draft_id): candidate
                for candidate in list_draft_states(app_dir=self.store.app_dir)
            }
        )
        matching_account_ids = {
            candidate.account_id
            for candidate in states_by_key.values()
            if candidate.course_id in course_ids or candidate.draft_id in course_ids
        }
        if len(matching_account_ids) != 1:
            return

        account_id = matching_account_ids.pop()
        if account_id in self._account_labels:
            return

        self.store.set_active_account(
            account_id=account_id,
            screen_name=screen_name,
        )
        for key, candidate in tuple(self._draft_states_by_key.items()):
            if candidate.account_id == account_id:
                self._draft_states_by_key[key] = replace(
                    candidate,
                    account_screen_name=screen_name,
                )

    def _select_account_without_draft(self, *, account_id: str) -> None:
        """Show an account with no recovered draft without retaining another account's pool.
        This keeps the live logged-in account reachable through the account cycle.
        """

        self._active_account_id = account_id
        self.store.set_active_account(account_id=account_id)
        self._active_account_label = self._account_label(account_id=account_id)
        self._event_name = None
        self._set_code = None
        self._draft_id = None
        self._pick_label = "—"
        self._pool_size = 0
        self._pool_grp_ids = ()
        self._last_picks = []
        self._current_pack_event = None
        self._current_pack = None
        self._pair_label = "open"
        self._commitment_label = "0% open"
        self._view_mode = "pack"
        self._forced_pair = None
        self._build_pair_label = "—"
        self._build_text = "Build view: no picked cards yet."
        self._build_error = None
        self._build_action_status = None
        self._backtest_text = "Backtest view: complete a draft, then press t."
        self._backtest_error = None
        self._backtest_action_status = None
        self._last_build_signature = None
        self._build_spell_sort_mode = "curve"
        self._build_show_details = False
        self._clear_build_render_state()

    def _select_draft_state(self, *, state: DraftState) -> None:
        self._remember_draft_state(state=state)
        self._active_account_id = state.account_id
        self.store.set_active_account(
            account_id=state.account_id,
            screen_name=state.account_screen_name,
        )
        self._active_account_label = self._account_label(account_id=state.account_id)
        self._event_name = state.event_name
        self._set_code = state.set_code
        self._draft_id = state.draft_id
        self._pick_label = "complete" if state.completed else "recovered"
        self._pool_grp_ids = state.pool_grp_ids
        self._pool_size = len(state.pool_grp_ids)
        self._current_pack_event = None
        self._current_pack = None
        self._pair_label = "open"
        self._commitment_label = "0% recovered"
        self._forced_pair = None
        self._build_action_status = None
        self._backtest_action_status = None
        self._last_picks = [
            _format_card_name(card=self.card_database.lookup(grp_id=grp_id))
            for grp_id in state.pool_grp_ids[-5:]
        ]
        self._ensure_ratings_load_started(set_code=state.set_code)
        if self._rebuild_build_view():
            self._view_mode = "build"


    def _score_current_pack(self) -> None:
        event = self._current_pack_event
        if event is None:
            self._current_pack = None
            self._data_source = "unknown"
            return

        ratings_data = self._ratings_data_for_scoring(set_code=event.set_code)
        engine = PickEngine(ratings_data=ratings_data)
        self._current_pack = engine.score_pack(
            offered_grp_ids=event.offered_grp_ids,
            card_database=self.card_database,
            pool_grp_ids=event.pool_grp_ids,
            pick_index=_draft_pick_index(event=event),
        )
        commitment = self._current_pack.commitment
        self._pair_label = commitment.inferred_pair or "open"
        self._commitment_label = (
            f"{int(round(commitment.level * 100))}% {commitment.phase}"
        )
        self._pool_size = commitment.pool_size
        self._data_source = self._data_source_label(set_code=event.set_code)

    def _ratings_data_for_scoring(self, *, set_code: str) -> SeventeenLandsData | None:
        ratings_data = self._ratings_data_by_set.get(set_code)
        if ratings_data is None:
            return None

        return replace(ratings_data, pair_card_ratings_loader=None)

    def _data_source_label(self, *, set_code: str) -> str:
        if self._current_pack is None:
            return "unknown"

        label = self._current_pack.source_summary
        if set_code in self._loading_rating_sets:
            return f"{label} (loading ratings)"

        error_message = self._rating_errors_by_set.get(set_code)
        if error_message is not None:
            return f"{label} (ratings unavailable)"

        return label

    def _render_all(self) -> None:
        if not self.is_mounted:
            return

        self._update_responsive_visibility()
        self._render_pack_title()
        self._render_pack_table()
        self._render_sidebar()
        self._ensure_visible_focus()
        self._render_status_bar()

    def _update_responsive_visibility(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar.display = self.size.width >= SIDEBAR_MIN_WIDTH

    def _render_pack_title(self) -> None:
        title = self.query_one("#pack-title", Static)
        if self._view_mode == "build":
            if self._build_error is not None and self._build_pair_label == "—":
                title.update("Build view unavailable — metadata or playable count issue")
                return

            build_pair_label = _format_pair_label(
                pair=self._build_pair_label,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            override = "automatic"
            if self._forced_pair is not None:
                forced_pair = _format_pair_label(
                    pair=self._forced_pair,
                    mana_icons_enabled=self.mana_icons_enabled,
                )
                override = f"forced {forced_pair}"

            detail = "details" if self._build_show_details else "compact"
            title.update(
                f"Build view — pair {build_pair_label} ({override}); "
                f"spells {self._build_spell_sort_mode}; {detail}; "
                "scroll ↑/↓ PgUp/PgDn"
            )
            return

        if self._view_mode == "backtest":
            if self._backtest_error is not None:
                title.update("Backtest view unavailable — saved draft state issue")
            else:
                title.update(
                    "Backtest view — "
                    f"{ranking_label(ranking_mode=self.sort_mode)} "
                    "recommendations vs actual picks; scroll ↑/↓ PgUp/PgDn"
                )
            return

        if self._current_pack_event is None:
            if self._pick_label == "complete":
                title.update("Draft complete")
            else:
                title.update("Waiting for a Quick Draft pack…")
            return

        event = self._current_pack_event
        title.update(
            "Available cards — Pack "
            f"{event.pack_number + 1} "
            "Pick "
            f"{event.pick_number + 1} "
            f"— ranked by {SORT_LABELS[self.sort_mode]}"
        )

    def _render_pack_table(self) -> None:
        table = self.query_one("#pack-table", DataTable)
        build_scroll = self.query_one("#build-scroll", VerticalScroll)
        build_view = self.query_one("#build-view", Static)
        table.display = self._view_mode == "pack"
        build_scroll.display = self._view_mode in {"build", "backtest"}
        build_view.update(self._active_text_view())
        if self._view_mode in {"build", "backtest"}:
            self._visible_column_keys = ()
            return

        previous_row_key, previous_row_index = self._capture_table_cursor(table=table)
        table.clear(columns=True)
        column_keys = self._column_keys_for_width()
        self._visible_column_keys = column_keys
        for column_key in column_keys:
            table.add_column(
                COLUMN_LABELS[column_key],
                width=COLUMN_WIDTHS[column_key],
                key=column_key,
            )

        if self._current_pack is None:
            return

        for rank, scored_card in enumerate(self._sorted_cards(), start=1):
            row = _row_cells(
                rank=rank,
                scored_card=scored_card,
                column_keys=column_keys,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            table.add_row(
                *row,
                key=f"{rank}-{scored_card.card.grp_id}-{scored_card.original_index}",
            )

        self._restore_table_cursor(
            table=table,
            row_key=previous_row_key,
            row_index=previous_row_index,
        )

    def _capture_table_cursor(self, *, table: DataTable) -> tuple[str | None, int]:
        if table.row_count == 0:
            return None, 0

        row_index = max(0, table.cursor_coordinate.row)
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:  # pragma: no cover - defensive Textual boundary.
            return None, row_index

        return str(cell_key.row_key.value), row_index

    def _restore_table_cursor(
        self,
        *,
        table: DataTable,
        row_key: str | None,
        row_index: int,
    ) -> None:
        if table.row_count == 0:
            return

        target_row = min(max(row_index, 0), table.row_count - 1)
        if row_key is not None:
            try:
                target_row = table.get_row_index(row_key)
            except Exception:  # pragma: no cover - defensive Textual boundary.
                pass

        table.move_cursor(row=target_row, column=0, animate=False)

    def _move_pack_cursor(self, *, delta: int) -> None:
        table = self.query_one("#pack-table", DataTable)
        self._move_pack_cursor_to(row=table.cursor_coordinate.row + delta)

    def _move_pack_cursor_to(self, *, row: int) -> None:
        if self._view_mode != "pack":
            return

        table = self.query_one("#pack-table", DataTable)
        if table.row_count == 0:
            return

        target_row = min(max(row, 0), table.row_count - 1)
        table.focus()
        table.move_cursor(row=target_row, column=0, animate=False)
        self._render_focused_card_details()

    def _move_build_card_cursor(self, *, delta: int) -> None:
        if not self._build_focus_cards:
            return

        target_index = self._build_focused_card_index + delta
        self._build_focused_card_index = min(
            max(target_index, 0),
            len(self._build_focus_cards) - 1,
        )
        self._refresh_build_text_from_render_state()
        self.query_one("#build-scroll", VerticalScroll).focus()
        self.query_one("#build-view", Static).update(self._build_text)
        self._render_focused_card_details()

    def _pack_cursor_page_size(self) -> int:
        table = self.query_one("#pack-table", DataTable)
        return max(1, table.size.height - 3)

    def _render_sidebar(self) -> None:
        pool_summary = self.query_one("#pool-summary", Static)
        pool_summary.display = self._view_mode not in {"build", "backtest"}
        if pool_summary.display:
            event_text = self._event_name or "unknown event"
            draft_text = self._draft_id or "unknown draft"
            color_bar = _pool_color_distribution_bar(
                pool_grp_ids=self._pool_grp_ids,
                card_database=self.card_database,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            curve = _pool_curve_sparkline(
                pool_grp_ids=self._pool_grp_ids,
                card_database=self.card_database,
            )
            override_label = self._build_override_label()
            inferred_pair = _format_pair_label(
                pair=self._pair_label,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            build_pair = _format_pair_label(
                pair=self._build_pair_label,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            override = _format_pair_label(
                pair=override_label,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            pool_summary.update(
                "Pool summary\n"
                f"Set: {format_set_label(set_code=self._set_code)}\n"
                f"Event: {event_text}\n"
                f"Draft: {draft_text}\n"
                f"Pool size: {self._pool_size}\n"
                f"Inferred pair: {inferred_pair}\n"
                f"Build pair: {build_pair}\n"
                f"Override: {override}\n"
                f"Build action: {self._build_action_label()}\n"
                f"{self._metadata_status_text()}\n"
                f"{color_bar}\n"
                f"{curve}"
            )

        self._render_focused_card_details()

    def _render_focused_card_details(self) -> None:
        try:
            focused_card = self.query_one("#focused-card", Static)
        except NoMatches:
            return

        selected = self._focused_card_details()
        if selected is None:
            self._render_card_image_preview(card=None)
            focused_card.update(
                "Focused card details\n"
                "Use ↑/↓/←/→ in the card list to browse card details here."
            )
            return

        section, rank, total_count, scored_card, quantity = selected
        card = scored_card.card
        self._render_card_image_preview(card=card)
        type_line = _format_card_types(
            card=card,
            mana_icons_enabled=self.mana_icons_enabled,
        )
        quantity_line = f"Quantity: {quantity}\n" if quantity > 1 else ""
        color_label = _format_card_colors(
            card=card,
            mana_icons_enabled=self.mana_icons_enabled,
            long_colorless=True,
        )
        focused_card.update(
            "Focused card details\n"
            f"{section} {rank}/{total_count}\n"
            f"{_format_card_name(card=card)}\n"
            f"{quantity_line}"
            f"Colors: {color_label}\n"
            f"Mana value: {_format_mana_value(card=card)}\n"
            f"Type: {type_line}\n"
            f"17L WR: {_format_win_rate(scored_card=scored_card)}\n"
            f"17L Grade: {_format_letter_grade(scored_card=scored_card)}\n"
            f"DG Score: {scored_card.score}\n"
            f"Color fit: {_format_color_fit(scored_card=scored_card)}\n"
            f"ALSA (avg last seen): {_format_alsa(scored_card=scored_card)}\n"
            f"Data source: {_format_tui_source_label(scored_card=scored_card)}"
        )

    def _render_card_image_preview(self, *, card: CardInfo | None) -> None:
        image_panel = self.query_one("#card-image-preview", Static)
        if not self._card_image_preview_enabled:
            image_panel.display = False
            return

        sidebar = self.query_one("#sidebar", Vertical)
        if not sidebar.display:
            image_panel.display = False
            return

        if TgpImage is None:
            image_panel.display = True
            image_panel.update(
                "Image preview unavailable\n"
                "Install the textual-image package to render Kitty images."
            )
            return

        if card is None:
            image_panel.display = False
            return

        image_panel.display = True
        image_uri = self._card_image_uri_for_card(card=card)
        if image_uri is None:
            self._render_pending_card_image_uri(image_panel=image_panel, card=card)
            return
        image_path = self._card_image_paths_by_uri.get(image_uri)
        if image_path is not None and image_path.exists():
            self._show_card_image(
                image_panel=image_panel,
                image_path=image_path,
                card=card,
                image_uri=image_uri,
            )
            return

        cached_path = _cached_card_image_path(
            cache_dir=self._card_image_cache_dir,
            image_uri=image_uri,
        )
        if cached_path.exists():
            self._card_image_paths_by_uri[image_uri] = cached_path
            self._show_card_image(
                image_panel=image_panel,
                image_path=cached_path,
                card=card,
                image_uri=image_uri,
            )
            return

        failure = self._card_image_failures_by_uri.get(image_uri)
        if failure is not None:
            image_panel.update(
                "Image preview unavailable\n"
                f"{_format_card_name(card=card)}\n"
                f"{failure}"
            )
            return

        image_panel.update(
            "Loading image preview…\n"
            f"{_format_card_name(card=card)}"
        )
        if image_uri not in self._loading_card_image_uris:
            self._loading_card_image_uris.add(image_uri)
            self._fetch_card_image_worker(image_uri)

    def _card_image_uri_for_card(self, *, card: CardInfo) -> str | None:
        if card.image_uri is not None:
            return card.image_uri

        return self._card_image_uris_by_grp_id.get(card.grp_id)

    def _render_pending_card_image_uri(
        self,
        *,
        image_panel: Static,
        card: CardInfo,
    ) -> None:
        if card.unknown:
            image_panel.display = False
            return

        image_uri = self.card_database.image_uri_for_name(name=card.name)
        if image_uri is not None:
            self._card_image_uris_by_grp_id[card.grp_id] = image_uri
            self._render_card_image_preview(card=card)
            return

        image_panel.update(
            "Image preview unavailable\n"
            f"{_format_card_name(card=card)}\n"
            "Image URL is not in the local Scryfall cache. Run refresh-data."
        )

    def _show_card_image(
        self,
        *,
        image_panel: Static,
        image_path: Path,
        card: CardInfo,
        image_uri: str,
    ) -> None:
        if TgpImage is None:
            return

        width = max(20, min(30, image_panel.size.width or 28))
        try:
            preview = TgpImage(
                str(image_path),
                width=width,
                height="auto",
            )
        except Exception as error:  # pragma: no cover - defensive renderer boundary.
            self._card_image_failures_by_uri[image_uri] = str(error)
            image_panel.update(
                "Image preview unavailable\n"
                f"{_format_card_name(card=card)}\n"
                f"{error}"
            )
            return

        image_panel.update(
            Group(
                Align.center(Text(f"Preview: {_format_card_name(card=card)}")),
                "",
                Align.center(preview),
            )
        )

    @work(thread=True, group="card-images")
    def _fetch_card_image_worker(self, image_uri: str) -> None:
        worker = get_current_worker()
        if worker.is_cancelled:
            return

        image_path = None
        error_message = None
        try:
            image_path = self.card_image_fetcher(
                image_uri,
                self._card_image_cache_dir,
            )
        except Exception as error:  # pragma: no cover - defensive network boundary.
            error_message = str(error)

        if worker.is_cancelled:
            return

        self.call_from_thread(
            self._finish_card_image_load,
            image_uri,
            image_path,
            error_message,
        )

    def _finish_card_image_load(
        self,
        image_uri: str,
        image_path: Path | None,
        error_message: str | None,
    ) -> None:
        self._loading_card_image_uris.discard(image_uri)
        if image_path is None:
            self._card_image_failures_by_uri[image_uri] = error_message or "fetch failed"
        elif not image_path.exists():
            self._card_image_failures_by_uri[image_uri] = "fetch failed"
        else:
            self._card_image_paths_by_uri[image_uri] = image_path
            self._card_image_failures_by_uri.pop(image_uri, None)

        self._render_focused_card_details()

    def _focused_card_details(
        self,
    ) -> tuple[str, int, int, ScoredCard, int] | None:
        if self._view_mode == "build":
            return self._focused_build_card()

        selected = self._focused_pack_card()
        if selected is None:
            return None

        rank, scored_card = selected
        return "Available card", rank, len(self._sorted_cards()), scored_card, 1

    def _focused_build_card(self) -> tuple[str, int, int, ScoredCard, int] | None:
        if self._view_mode != "build" or not self._build_focus_cards:
            return None

        self._build_focused_card_index = min(
            max(self._build_focused_card_index, 0),
            len(self._build_focus_cards) - 1,
        )
        card, quantity = self._build_focus_cards[self._build_focused_card_index]
        return (
            "Selected card",
            self._build_focused_card_index + 1,
            len(self._build_focus_cards),
            card,
            quantity,
        )

    def _focused_pack_card(self) -> tuple[int, ScoredCard] | None:
        if self._view_mode != "pack" or self._current_pack is None:
            return None

        table = self.query_one("#pack-table", DataTable)
        row_index = table.cursor_coordinate.row
        cards = self._sorted_cards()
        if row_index < 0 or row_index >= len(cards):
            return None

        return row_index + 1, cards[row_index]

    def _ensure_visible_focus(self) -> None:
        focused_id = None if self.focused is None else self.focused.id
        if focused_id is None:
            self._focus_primary_card_section()
            return

        if focused_id == "pack-table" and self._view_mode != "pack":
            self._focus_primary_card_section()
            return

        if focused_id == "build-scroll" and self._view_mode not in {"build", "backtest"}:
            self._focus_primary_card_section()

    def _focus_primary_card_section(self) -> None:
        if self._view_mode in {"build", "backtest"}:
            self.query_one("#build-scroll", VerticalScroll).focus()
            return

        self.query_one("#pack-table", DataTable).focus()

    def _build_override_label(self) -> str:
        if self._forced_pair is not None:
            return self._forced_pair

        if self._build_error is not None and self._build_pair_label == "—":
            return "unavailable"

        return "automatic"

    def _build_action_label(self) -> str:
        if self._build_action_status is None:
            return "not requested"

        return self._build_action_status

    def _render_status_bar(self) -> None:
        status = self.query_one("#status-bar", Static)
        pair_label = self._pair_label
        if self._view_mode == "build" and self._build_pair_label != "—":
            pair_label = self._build_pair_label
        pair_label = _format_pair_label(
            pair=pair_label,
            mana_icons_enabled=self.mana_icons_enabled,
        )

        if self._view_mode == "build":
            sort_label = f"Build sort: {self._build_spell_sort_mode}"
        elif self._view_mode == "backtest":
            sort_label = f"Backtest ranking: {SORT_LABELS[self.sort_mode]}"
        else:
            sort_label = f"Ranking: {SORT_LABELS[self.sort_mode]}"
        confidence_label = self._recommendation_confidence_label()
        confidence_text = (
            f"Confidence: {confidence_label} | "
            if confidence_label is not None
            else ""
        )
        icon_label = "on" if self.mana_icons_enabled else "off"
        text = (
            f"Account: {self._active_account_label} | "
            f"View: {self._view_mode} | "
            f"Pair: {pair_label} ({self._commitment_label}) | "
            f"Pick: {self._pick_label} | "
            f"Pool: {self._pool_size} | "
            f"Data: {self._data_source} | "
            f"{sort_label} | "
            f"{confidence_text}"
            f"Mana icons: {icon_label} | "
            f"{SEVENTEEN_LANDS_ATTRIBUTION}"
        )
        if self._forced_pair is not None:
            override = _format_pair_label(
                pair=self._forced_pair,
                mana_icons_enabled=self.mana_icons_enabled,
            )
            text = f"Override: {override} | {text}"

        unresolved_count = self._unresolved_metadata_count()
        if unresolved_count > 0:
            text = f"Warning: {unresolved_count} unresolved card metadata | {text}"

        if self._build_error is not None:
            text = f"Build: {self._build_error} | {text}"

        if self._build_action_status is not None:
            text = f"Build action: {self._build_action_status} | {text}"

        if self._backtest_action_status is not None:
            text = f"Backtest action: {self._backtest_action_status} | {text}"

        if self._backtest_error is not None and self._view_mode == "backtest":
            text = f"Backtest: {self._backtest_error} | {text}"

        if self._last_error is not None:
            text = f"Error: {self._last_error} | {text}"

        status.update(text)

    def _column_keys_for_width(self) -> tuple[str, ...]:
        show_secondary = (
            self.show_secondary_columns and self.size.width >= SECONDARY_COLUMN_MIN_WIDTH
        )
        if show_secondary:
            return PRIMARY_COLUMN_KEYS + SECONDARY_COLUMN_KEYS

        return PRIMARY_COLUMN_KEYS

    def _sorted_cards(self) -> tuple[ScoredCard, ...]:
        if self._current_pack is None:
            return ()

        return rank_scored_cards(
            cards=self._current_pack.cards,
            ranking_mode=self.sort_mode,
        )

    def _recommendation_confidence_label(self) -> str | None:
        if self._view_mode != "pack" or self._current_pack is None:
            return None

        return _recommendation_confidence_label(
            cards=self._sorted_cards(),
            ranking_mode=self.sort_mode,
            phase=self._current_pack.commitment.phase,
        )

    def _current_build_pool(self) -> BuildPool | None:
        if self._set_code is None or not self._pool_grp_ids:
            return None

        return BuildPool(
            set_code=self._set_code,
            pool_grp_ids=self._pool_grp_ids,
            source_label="live draft",
            account_id=self._active_account_id,
            draft_id=self._draft_id,
        )

    def _metadata_status_text(self) -> str:
        visible_count = len(self._visible_metadata_grp_ids())
        if visible_count == 0:
            return "Metadata: waiting"

        unresolved_count = self._unresolved_metadata_count()
        if unresolved_count == 0:
            return "Metadata: complete"

        return f"Metadata warning: {unresolved_count} unresolved card metadata"

    def _unresolved_metadata_count(self) -> int:
        unresolved_grp_ids = self.card_database.unresolved_grp_ids(
            grp_ids=self._visible_metadata_grp_ids(),
        )
        return len(unresolved_grp_ids)

    def _visible_metadata_grp_ids(self) -> tuple[int, ...]:
        grp_ids = list(self._pool_grp_ids)
        if self._current_pack_event is not None:
            grp_ids.extend(self._current_pack_event.offered_grp_ids)

        return tuple(grp_ids)

    def _active_text_view(self) -> str:
        if self._view_mode == "backtest":
            return self._backtest_text

        return self._build_text

    def _rebuild_backtest_view(self) -> bool:
        state = self._current_backtest_state()
        if state is None:
            self._backtest_error = "no persisted draft state for current draft"
            self._backtest_text = (
                "Backtest view unavailable: no persisted draft state for the "
                "current draft. Watch or replay a draft first."
            )
            return False

        report = generate_backtest_report(
            state=state,
            card_database=self.card_database,
            ratings_data=self._ratings_data_for_scoring(set_code=state.set_code),
            ranking_mode=self.sort_mode,
        )
        self._backtest_error = None
        self._backtest_text = format_backtest_report(report).rstrip("\n")
        return True

    def _current_backtest_state(self) -> DraftState | None:
        if self._active_account_id is not None and self._draft_id is not None:
            key = (self._active_account_id, self._draft_id)
            cached = self._draft_states_by_key.get(key)
            if cached is not None:
                return cached

            for state in self._available_draft_states():
                if (state.account_id, state.draft_id) == key:
                    self._remember_draft_state(state=state)
                    return state

        matching_states = tuple(
            state
            for state in self._available_draft_states()
            if self._event_name is not None
            and state.event_name == self._event_name
            and state.set_code == self._set_code
        )
        if not matching_states:
            return None

        return max(matching_states, key=_latest_draft_state_sort_key)

    def _rebuild_build_view(self) -> bool:
        pool = self._current_build_pool()
        if pool is None:
            self._build_error = "no picked cards yet"
            self._build_text = "Build view: no picked cards yet."
            self._build_pair_label = "—"
            self._last_build_signature = None
            self._clear_build_render_state()
            return False

        try:
            selection, build_sheet = build_deck_from_pool(
                pool=pool,
                card_database=self.card_database,
                ratings_data=self._ratings_data_for_scoring(set_code=pool.set_code),
                forced_pair=self._forced_pair,
                allow_splash=False,
            )
        except DeckBuilderError as error:
            self._build_error = str(error)
            self._build_text = _format_tui_build_error(
                pool=pool,
                card_database=self.card_database,
                error=str(error),
                width=self._build_text_width(),
                mana_icons_enabled=self.mana_icons_enabled,
            )
            self._build_pair_label = "—"
            self._last_build_signature = None
            self._clear_build_render_state()
            return True

        self._build_error = None
        self._last_error = None
        self._build_pair_label = selection.chosen.pair
        self._last_build_signature = self._build_signature(pool=pool)
        self._build_render_pool = pool
        self._build_render_selection = selection
        self._build_render_spell_selection = build_sheet.spell_selection
        self._build_render_mana_base = build_sheet.mana_base
        self._refresh_build_text_from_render_state()
        return True

    def _refresh_build_text_from_render_state(self) -> None:
        if (
            self._build_render_pool is None
            or self._build_render_selection is None
            or self._build_render_spell_selection is None
            or self._build_render_mana_base is None
        ):
            return

        self._build_focus_cards = _tui_selected_spell_groups(
            spell_selection=self._build_render_spell_selection,
            spell_sort_mode=self._build_spell_sort_mode,
        )
        if not self._build_focus_cards:
            self._build_focused_card_index = 0
        else:
            self._build_focused_card_index = min(
                max(self._build_focused_card_index, 0),
                len(self._build_focus_cards) - 1,
            )

        self._build_text = _format_tui_build_result(
            pool=self._build_render_pool,
            selection=self._build_render_selection,
            spell_selection=self._build_render_spell_selection,
            mana_base=self._build_render_mana_base,
            card_database=self.card_database,
            spell_sort_mode=self._build_spell_sort_mode,
            show_details=self._build_show_details,
            focused_card_index=self._build_focused_card_index,
            width=self._build_text_width(),
            mana_icons_enabled=self.mana_icons_enabled,
        )

    def _clear_build_render_state(self) -> None:
        self._build_focus_cards = ()
        self._build_focused_card_index = 0
        self._build_render_pool = None
        self._build_render_selection = None
        self._build_render_spell_selection = None
        self._build_render_mana_base = None

    def _build_signature(self, *, pool: BuildPool) -> BuildSignature:
        return (pool.set_code, pool.pool_grp_ids, self._forced_pair)

    def _record_build_action_result(self, *, success_message: str) -> None:
        if self._build_error is None:
            self._build_action_status = success_message
            return

        self._build_action_status = f"cannot build — {self._build_error}"

    def _build_text_width(self) -> int:
        sidebar_width = max(0, self.query_one("#sidebar", Vertical).size.width)
        return max(60, self.size.width - sidebar_width - 4)

    def _next_forced_pair(self) -> str:
        current_pair = self._forced_pair
        if current_pair is None and self._build_pair_label in COLOR_PAIRS:
            current_pair = self._build_pair_label

        if current_pair not in COLOR_PAIRS:
            return COLOR_PAIRS[0]

        index = COLOR_PAIRS.index(current_pair)
        return COLOR_PAIRS[(index + 1) % len(COLOR_PAIRS)]

    def _display_account_id(self, *, account_id: str | None) -> str | None:
        if (
            account_id is None
            and self.parser.pending_login_screen_name is not None
        ):
            return None

        return account_id or self._active_account_id

    def _account_label(self, *, account_id: str | None) -> str:
        client_id = account_id or self._active_account_id
        if client_id is None:
            return "unknown"

        return self._account_labels.get(client_id, client_id)

    def _record_error(self, message: str) -> None:
        self._last_error = message
        self._render_all()


def _card_image_preview_enabled(*, env: Mapping[str, str]) -> bool:
    if TgpImage is None:
        return False

    override = env.get(CARD_IMAGE_PREVIEW_ENV)
    if override is not None:
        return override.strip().casefold() in {"1", "true", "yes", "on"}

    term_program = env.get("TERM_PROGRAM", "").casefold()
    if term_program in {"ghostty", "kitty", "wezterm"}:
        return True

    term = env.get("TERM", "").casefold()
    if "kitty" in term or "ghostty" in term:
        return True

    return bool(
        env.get("KITTY_WINDOW_ID")
        or env.get("GHOSTTY_RESOURCES_DIR")
        or env.get("WEZTERM_EXECUTABLE")
    )


def _card_image_cache_dir(*, app_dir: PathInput | None) -> Path:
    store = DraftPoolStore(app_dir=app_dir)
    return store.root.parent / CARD_IMAGE_CACHE_DIR_NAME


def _fetch_card_image(image_uri: str, cache_dir: Path) -> Path:
    image_path = _cached_card_image_path(cache_dir=cache_dir, image_uri=image_uri)
    if image_path.exists():
        return image_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        image_uri,
        headers={
            "Accept": "image/*,*/*;q=0.8",
            "User-Agent": SCRYFALL_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=CARD_IMAGE_PREVIEW_TIMEOUT_SECONDS,
        ) as response:
            image_data = response.read(CARD_IMAGE_PREVIEW_MAX_BYTES + 1)
    except urllib.error.URLError as error:
        raise RuntimeError(f"image fetch failed: {error}") from error

    if len(image_data) > CARD_IMAGE_PREVIEW_MAX_BYTES:
        raise RuntimeError("image fetch failed: response too large")

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=cache_dir,
        delete=False,
    ) as temporary_file:
        temporary_file.write(image_data)
        temporary_path = Path(temporary_file.name)

    temporary_path.replace(image_path)
    return image_path


def _cached_card_image_path(*, cache_dir: Path, image_uri: str) -> Path:
    digest = hashlib.sha256(image_uri.encode("utf-8")).hexdigest()
    extension = _card_image_extension(image_uri=image_uri)
    return cache_dir / f"{digest}{extension}"


def _card_image_extension(*, image_uri: str) -> str:
    parsed_uri = urllib.parse.urlparse(image_uri)
    extension = Path(parsed_uri.path).suffix.lower()
    if extension in CARD_IMAGE_FILE_EXTENSIONS:
        return extension

    return ".jpg"


_BUILD_COLUMN_MIN_WIDTH = 34
_BUILD_COLUMN_MAX_WIDTH = 80


def _format_tui_build_result(
    *,
    pool: BuildPool,
    selection: PairSelection,
    spell_selection: SpellSelection,
    mana_base: ManaBase,
    card_database: CardDatabase,
    spell_sort_mode: str,
    show_details: bool,
    focused_card_index: int,
    width: int,
    mana_icons_enabled: bool = False,
) -> str:
    chosen_label = "forced" if selection.forced_pair is not None else "automatic"
    color_pair = _format_pair_label(
        pair=selection.chosen.pair,
        mana_icons_enabled=mana_icons_enabled,
    )
    counts = spell_selection.counts
    lines = [
        "[bold]Suggested deck[/bold]",
        "",
        f"Set: {format_set_label(set_code=pool.set_code)}",
        f"Color pair: {color_pair} ({chosen_label})",
        (
            f"Deck: {mana_base.deck_size} cards — "
            f"{mana_base.spell_count} spells, {mana_base.land_count} lands"
        ),
        f"Average mana value: {mana_base.average_mana_value:.2f}",
        (
            f"Creatures: {counts.creatures}; "
            f"Noncreatures: {counts.noncreatures}; Lands: {mana_base.land_count}"
        ),
        (
            "Ratings: rows show 17Lands WR and 17Lands-style grade from "
            "each source format."
        ),
        (
            "Keys: b checks build status; ↑/↓/←/→ or j/k browse cards; "
            "PgUp/PgDn page; s changes spell sort; c shows details/pool; "
            "p changes pair; m toggles Mana icons"
        ),
        "",
    ]
    lines.extend(
        _format_tui_selected_spells(
            spell_selection=spell_selection,
            spell_sort_mode=spell_sort_mode,
            focused_card_index=focused_card_index,
            width=width,
            mana_icons_enabled=mana_icons_enabled,
        )
    )
    lines.append("")
    lines.extend(
        _format_tui_lands(
            mana_base=mana_base,
            mana_icons_enabled=mana_icons_enabled,
        )
    )
    if show_details:
        lines.append("")
        lines.extend(
            _format_tui_build_context(
                pool=pool,
                selection=selection,
                spell_selection=spell_selection,
                mana_base=mana_base,
                mana_icons_enabled=mana_icons_enabled,
            )
        )
        lines.append("")
        lines.extend(
            _format_tui_spell_counts(
                spell_selection=spell_selection,
                mana_icons_enabled=mana_icons_enabled,
            )
        )
        lines.append("")
        lines.extend(
            _format_tui_picked_pool(
                pool=pool,
                card_database=card_database,
                width=width,
                mana_icons_enabled=mana_icons_enabled,
            )
        )
        lines.append("")
        lines.extend(
            _format_tui_pair_scores(
                selection=selection,
                mana_icons_enabled=mana_icons_enabled,
            )
        )
        lines.append("")
        lines.extend(
            _format_tui_bench(
                selection=spell_selection,
                width=width,
                mana_icons_enabled=mana_icons_enabled,
            )
        )
    else:
        lines.append("")
        lines.append(
            "Details hidden: press c for build context, picked pool, "
            "color-pair reasoning, structure checks, and bench cuts."
        )

    return "\n".join(lines) + "\n"


def _format_tui_build_context(
    *,
    pool: BuildPool,
    selection: PairSelection,
    spell_selection: SpellSelection,
    mana_base: ManaBase,
    mana_icons_enabled: bool = False,
) -> list[str]:
    lines = [
        "Build context",
        _format_tui_curve_summary(spells=spell_selection.spells),
        f"Pool: {pool.source_label}",
        f"Pool size: {selection.pool_size} cards",
    ]
    if pool.account_id is not None:
        lines.append(f"Account: {pool.account_id}")

    if pool.draft_id is not None:
        lines.append(f"Draft: {pool.draft_id}")

    mana_pips = _format_plain_color_counts(
        mana_base.pip_counts,
        mana_icons_enabled=mana_icons_enabled,
    )
    mana_sources = _format_plain_color_counts(
        mana_base.source_counts,
        mana_icons_enabled=mana_icons_enabled,
    )
    lines.extend([
        f"Mana pips: {mana_pips}",
        f"Mana sources: {mana_sources}",
    ])
    return lines


def _format_tui_build_error(
    *,
    pool: BuildPool,
    card_database: CardDatabase,
    error: str,
    width: int,
    mana_icons_enabled: bool = False,
) -> str:
    lines = [
        f"Build view unavailable: {error}",
        "",
    ]
    lines.extend(
        _format_tui_picked_pool(
            pool=pool,
            card_database=card_database,
            width=width,
            mana_icons_enabled=mana_icons_enabled,
        )
    )
    return "\n".join(lines) + "\n"


def _format_tui_picked_pool(
    *,
    pool: BuildPool,
    card_database: CardDatabase,
    width: int,
    mana_icons_enabled: bool = False,
) -> list[str]:
    lines = [f"Picked pool ({len(pool.pool_grp_ids)})"]
    if not pool.pool_grp_ids:
        return lines + ["- none"]

    for index, grp_id in enumerate(pool.pool_grp_ids, start=1):
        card = card_database.lookup(grp_id=grp_id)
        lines.append(
            _clip(
                text=_format_tui_picked_card(
                    index=index,
                    card=card,
                    mana_icons_enabled=mana_icons_enabled,
                ),
                width=width,
            )
        )

    return lines


def _format_tui_picked_card(
    *,
    index: int,
    card: CardInfo,
    mana_icons_enabled: bool = False,
) -> str:
    marker = "[unresolved] " if card.unknown else ""
    color_label = _format_card_colors(
        card=card,
        mana_icons_enabled=mana_icons_enabled,
        long_colorless=True,
    )
    mana_value = _format_mana_value(card=card)
    card_name = _format_card_name(card=card)
    return f"{index:02d}. {marker}{card_name} | Colors {color_label} | MV {mana_value}"


def _format_tui_curve_summary(*, spells: tuple[ScoredCard, ...]) -> str:
    counts = [0 for _ in CURVE_BUCKET_LABELS]
    unknown_count = 0
    for card in spells:
        mana_value = card.card.mana_value
        if mana_value is None:
            unknown_count += 1
            continue

        counts[_curve_bucket(mana_value=mana_value)] += 1

    parts = [
        f"{label}: {counts[index]}"
        for index, label in enumerate(CURVE_BUCKET_LABELS)
    ]
    if unknown_count > 0:
        parts.append(f"?: {unknown_count}")

    return "Mana curve: " + " | ".join(parts)


def _format_tui_selected_spells(
    *,
    spell_selection: SpellSelection,
    spell_sort_mode: str,
    focused_card_index: int,
    width: int,
    mana_icons_enabled: bool = False,
) -> list[str]:
    groups = _tui_selected_spell_groups(
        spell_selection=spell_selection,
        spell_sort_mode=spell_sort_mode,
    )
    if spell_sort_mode == "score":
        return _format_tui_spell_columns(
            title="Selected spells by score",
            groups=groups,
            total_count=len(spell_selection.spells),
            focused_card_index=focused_card_index,
            width=width,
            mana_icons_enabled=mana_icons_enabled,
        )

    if spell_sort_mode == "name":
        return _format_tui_spell_columns(
            title="Selected spells by name",
            groups=groups,
            total_count=len(spell_selection.spells),
            focused_card_index=focused_card_index,
            width=width,
            mana_icons_enabled=mana_icons_enabled,
        )

    return _format_tui_spell_curve(
        groups=groups,
        total_count=len(spell_selection.spells),
        focused_card_index=focused_card_index,
        width=width,
        mana_icons_enabled=mana_icons_enabled,
    )


def _tui_selected_spell_groups(
    *,
    spell_selection: SpellSelection,
    spell_sort_mode: str,
) -> tuple[TuiCardQuantityGroup, ...]:
    if spell_sort_mode == "score":
        return _group_tui_spell_cards(
            cards=tuple(sorted(
                spell_selection.spells,
                key=lambda card: (
                    -card.score,
                    _format_mana_value(card=card.card),
                    card.card.name,
                ),
            )),
        )

    if spell_sort_mode == "name":
        return _group_tui_spell_cards(
            cards=tuple(sorted(
                spell_selection.spells,
                key=lambda card: card.card.name,
            )),
        )

    return _group_tui_spell_cards(
        cards=tuple(sorted(spell_selection.spells, key=_tui_spell_curve_sort_key)),
    )


def _format_tui_spell_curve(
    *,
    groups: tuple[TuiCardQuantityGroup, ...],
    total_count: int,
    focused_card_index: int,
    width: int,
    mana_icons_enabled: bool = False,
) -> list[str]:
    groups_by_bucket: dict[str, list[tuple[int, TuiCardQuantityGroup]]] = {}
    for index, group in enumerate(groups):
        groups_by_bucket.setdefault(_mana_value_bucket(card=group[0]), []).append(
            (index, group),
        )

    blocks: list[list[str]] = []
    for bucket in _ordered_mana_buckets(groups={
        key: [card for _, (card, _) in values]
        for key, values in groups_by_bucket.items()
    }):
        indexed_groups = groups_by_bucket[bucket]
        bucket_count = sum(quantity for _, (_, quantity) in indexed_groups)
        block = [f"MV {bucket} ({bucket_count})"]
        block.extend(
            _format_tui_spell_card(
                card=card,
                quantity=quantity,
                focused=index == focused_card_index,
                show_focus_marker=True,
                mana_icons_enabled=mana_icons_enabled,
            )
            for index, (card, quantity) in indexed_groups
        )
        blocks.append(block)

    return [f"Selected spells by mana value ({total_count})"] + _columnize_blocks(
        blocks=blocks,
        width=width,
    )


def _format_tui_spell_columns(
    *,
    title: str,
    groups: tuple[TuiCardQuantityGroup, ...],
    total_count: int,
    focused_card_index: int,
    width: int,
    mana_icons_enabled: bool = False,
) -> list[str]:
    blocks = [
        [
            _format_tui_spell_card(
                card=card,
                quantity=quantity,
                focused=index == focused_card_index,
                show_focus_marker=True,
                mana_icons_enabled=mana_icons_enabled,
            )
        ]
        for index, (card, quantity) in enumerate(groups)
    ]
    return [f"{title} ({total_count})"] + _columnize_blocks(blocks=blocks, width=width)


def _columnize_blocks(*, blocks: list[list[str]], width: int) -> list[str]:
    if not blocks:
        return ["- none"]

    column_count = max(1, min(2, width // _BUILD_COLUMN_MIN_WIDTH))
    column_width = min(
        _BUILD_COLUMN_MAX_WIDTH,
        max(_BUILD_COLUMN_MIN_WIDTH, width // column_count),
    )
    columns: list[list[str]] = [[] for _ in range(column_count)]
    heights = [0 for _ in range(column_count)]
    for block in blocks:
        column_index = min(range(column_count), key=lambda index: heights[index])
        if columns[column_index]:
            columns[column_index].append("")
            heights[column_index] += 1

        columns[column_index].extend(block)
        heights[column_index] += len(block)

    max_height = max(len(column) for column in columns)
    lines: list[str] = []
    for row_index in range(max_height):
        parts = []
        for column in columns:
            text = column[row_index] if row_index < len(column) else ""
            parts.append(_clip(text=text, width=column_width - 2).ljust(column_width))

        lines.append("".join(parts).rstrip())

    return lines


def _format_tui_lands(
    *,
    mana_base: ManaBase,
    mana_icons_enabled: bool = False,
) -> list[str]:
    lines = [f"Lands: {mana_base.land_count} ({mana_base.reason})"]
    basics = ", ".join(
        f"{basic.count} {basic.name}" for basic in mana_base.basic_lands
    )
    if basics:
        lines.append(f"Basics: {basics}")
    else:
        lines.append("Basics: none")

    if mana_base.nonbasic_lands:
        nonbasic_parts = []
        for land in mana_base.nonbasic_lands:
            source_label = _format_color_label(
                colors=land.source_colors,
                mana_icons_enabled=mana_icons_enabled,
                long_colorless=False,
            )
            nonbasic_parts.append(f"{land.card.name} ({source_label} source)")

        lines.append(f"Nonbasics: {'; '.join(nonbasic_parts)}")
    else:
        lines.append("Nonbasics: none")

    return lines


def _format_tui_spell_counts(
    *,
    spell_selection: SpellSelection,
    mana_icons_enabled: bool = False,
) -> list[str]:
    counts = spell_selection.counts
    constraints = spell_selection.constraints
    pair = _format_pair_label(
        pair=spell_selection.pair,
        mana_icons_enabled=mana_icons_enabled,
    )
    return [
        "Structure checks",
        f"Eligible spells for {pair}: {spell_selection.eligible_count}",
        f"Selected spells: {counts.total}/{spell_selection.requested_spell_count}",
        (
            f"Creatures: {counts.creatures} "
            f"(target {constraints.creature_floor}-{constraints.creature_ceiling})"
        ),
        f"Two-drops: {counts.two_drops} (minimum {constraints.minimum_two_drops})",
        f"Expensive spells: {counts.expensive} (soft cap {constraints.maximum_expensive_spells})",
        f"Applied relaxations: {_format_tui_relaxations(spell_selection.applied_relaxations)}",
    ]


def _format_tui_pair_scores(
    *,
    selection: PairSelection,
    mana_icons_enabled: bool = False,
) -> list[str]:
    lines = [
        "Color-pair reasoning",
        (
            "This diagnostic compares playable cards with 17Lands color-pair "
            "context; it is not another decklist."
        ),
    ]
    for score in selection.ranked_scores:
        pair = _format_pair_label(
            pair=score.pair,
            mana_icons_enabled=mana_icons_enabled,
        )
        lines.append(
            f"- {pair}: {score.playable_count} playable cards; "
            f"pair strength {score.blended_score:.2f}; "
            f"17Lands WR {_format_tui_win_rate(score.pair_win_rate)}; "
            f"top {selection.target_spell_count} sum {score.playable_score_sum:.2f}"
        )

    return lines


def _format_tui_bench(
    *,
    selection: SpellSelection,
    width: int,
    mana_icons_enabled: bool = False,
) -> list[str]:
    lines = ["Bench"]
    if not selection.bench:
        return lines + ["- none"]

    for card, quantity in _group_tui_spell_cards(cards=selection.bench):
        lines.append(
            _clip(
                text=_format_tui_spell_card(
                    card=card,
                    quantity=quantity,
                    mana_icons_enabled=mana_icons_enabled,
                ),
                width=width,
            )
        )

    return lines


def _format_tui_spell_card(
    *,
    card: ScoredCard,
    quantity: int = 1,
    focused: bool = False,
    show_focus_marker: bool = False,
    mana_icons_enabled: bool = False,
) -> str:
    quantity_suffix = _format_tui_quantity_suffix(quantity=quantity)
    focus_marker = ""
    if show_focus_marker:
        focus_marker = "▶ " if focused else "  "

    color_label = _format_card_colors(
        card=card.card,
        mana_icons_enabled=mana_icons_enabled,
        long_colorless=False,
    )
    return (
        f"{focus_marker}"
        f"{_format_win_rate(scored_card=card):>6} "
        f"{_format_letter_grade(scored_card=card):>2} "
        f"{card.score:>2} "
        f"{card.card.name} ({color_label})"
        f"{quantity_suffix}"
    )


def _group_tui_spell_cards(
    *,
    cards: Iterable[ScoredCard],
) -> tuple[TuiCardQuantityGroup, ...]:
    grouped_cards: dict[TuiCardQuantityKey, TuiCardQuantityGroup] = {}
    for card in cards:
        quantity_key = _tui_card_quantity_key(card=card.card)
        existing_group = grouped_cards.get(quantity_key)
        if existing_group is None:
            grouped_cards[quantity_key] = (card, 1)
            continue

        representative, quantity = existing_group
        grouped_cards[quantity_key] = (representative, quantity + 1)

    return tuple(grouped_cards.values())


def _tui_card_quantity_key(*, card: CardInfo) -> TuiCardQuantityKey:
    if card.unknown:
        return ("unknown", str(card.grp_id))

    return ("name", " ".join(card.name.casefold().split()))


def _format_tui_quantity_suffix(*, quantity: int) -> str:
    if quantity <= 1:
        return ""

    return f" x{quantity}"


def _tui_spell_curve_sort_key(card: ScoredCard) -> tuple[float, int, str, int]:
    mana_value = 99.0 if card.card.mana_value is None else card.card.mana_value
    return (mana_value, -card.score, card.card.name, card.original_index)


def _mana_value_bucket(*, card: ScoredCard) -> str:
    mana_value = card.card.mana_value
    if mana_value is None:
        return "?"

    if mana_value >= 6:
        return "6+"

    return _format_mana_value(card=card.card)


def _ordered_mana_buckets(*, groups: dict[str, list[ScoredCard]]) -> tuple[str, ...]:
    ordered = tuple(label for label in ("0", "1", "2", "3", "4", "5", "6+") if label in groups)
    if "?" in groups:
        return ordered + ("?",)

    return ordered


def _format_plain_color_counts(
    counts: tuple[tuple[str, int], ...],
    *,
    mana_icons_enabled: bool = False,
) -> str:
    if not counts:
        return "none"

    parts = []
    for color, count in counts:
        label = _format_color_count_label(
            color=color,
            mana_icons_enabled=mana_icons_enabled,
        )
        parts.append(f"{label} {count}")

    return ", ".join(parts)


def _format_plain_colors(colors: tuple[str, ...]) -> str:
    return _format_color_label(colors=colors, mana_icons_enabled=False)


def _format_card_colors(
    *,
    card: CardInfo,
    mana_icons_enabled: bool = False,
    long_colorless: bool = True,
) -> str:
    if card.unknown:
        return "Unknown"

    return _format_color_label(
        colors=card.colors,
        mana_icons_enabled=mana_icons_enabled,
        long_colorless=long_colorless,
    )


def _format_color_label(
    *,
    colors: tuple[str, ...],
    mana_icons_enabled: bool = False,
    long_colorless: bool = True,
) -> str:
    if not colors:
        return _format_colorless_label(
            mana_icons_enabled=mana_icons_enabled,
            long_colorless=long_colorless,
        )

    if not mana_icons_enabled:
        return "".join(colors)

    return "".join(
        _format_mana_symbol(symbol=color, mana_icons_enabled=mana_icons_enabled)
        for color in colors
    )


def _format_pair_label(*, pair: str, mana_icons_enabled: bool = False) -> str:
    if not mana_icons_enabled:
        return pair

    if not pair or any(symbol not in MANA_ICON_GLYPHS for symbol in pair):
        return pair

    return "".join(
        _format_mana_symbol(symbol=symbol, mana_icons_enabled=mana_icons_enabled)
        for symbol in pair
    )


def _format_color_count_label(*, color: str, mana_icons_enabled: bool = False) -> str:
    if color == UNKNOWN_COLOR_KEY:
        return UNKNOWN_COLOR_KEY

    if color == COLORLESS_KEY:
        return _format_colorless_label(
            mana_icons_enabled=mana_icons_enabled,
            long_colorless=False,
        )

    return _format_pair_label(pair=color, mana_icons_enabled=mana_icons_enabled)


def _format_colorless_label(
    *,
    mana_icons_enabled: bool,
    long_colorless: bool,
) -> str:
    if not mana_icons_enabled:
        return "Colorless"

    fallback = "Colorless" if long_colorless else COLORLESS_KEY
    return f"{MANA_ICON_GLYPHS[COLORLESS_KEY]} {fallback}"


def _format_mana_symbol(*, symbol: str, mana_icons_enabled: bool) -> str:
    if not mana_icons_enabled:
        return symbol

    return MANA_ICON_GLYPHS.get(symbol, symbol)


def _format_card_types(
    *,
    card: CardInfo,
    mana_icons_enabled: bool = False,
) -> str:
    type_line = " ".join(card.types) if card.types else "Unknown"
    if card.unknown or not mana_icons_enabled:
        return type_line

    icons = [
        glyph
        for card_type, glyph in MANA_CARD_TYPE_GLYPHS.items()
        if any(card_type in type_part for type_part in card.types)
    ]
    if not icons:
        return type_line

    return f"{''.join(icons)} {type_line}"


def _format_tui_relaxations(relaxations: tuple[str, ...]) -> str:
    return ", ".join(relaxations) if relaxations else "none"


def _format_tui_win_rate(value: float | None) -> str:
    if value is None:
        return "unknown"

    return f"{value:.1%}"


def _clip(*, text: str, width: int) -> str:
    if len(text) <= width:
        return text

    if width <= 1:
        return "…"

    protected_suffix = _clip_protected_suffix(text=text)
    if protected_suffix and width > len(protected_suffix) + 1:
        prefix_width = width - len(protected_suffix) - 1
        return text[:prefix_width] + "…" + protected_suffix

    return text[: width - 1] + "…"


def _clip_protected_suffix(*, text: str) -> str:
    quantity_index = text.rfind(" x")
    if quantity_index == -1 or not text[quantity_index + 2 :].isdigit():
        return ""

    color_close_index = quantity_index - 1
    if color_close_index < 0 or text[color_close_index] != ")":
        return ""

    color_open_index = text.rfind("(", 0, color_close_index)
    if color_open_index == -1:
        return ""

    if color_open_index > 0 and text[color_open_index - 1] == " ":
        return text[color_open_index - 1 :]

    return text[color_open_index:]


_ERROR_EXIT_CODE = 1


def run_tui_watch(
    *,
    log_path: PathInput,
    card_database: CardDatabase,
    app_dir: PathInput | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    once: bool = False,
    startup_scan: bool = False,
    ratings_loader: RatingsLoader | None = None,
    mana_icons_enabled: bool = False,
) -> int:
    """Run Textual watch mode and return a process-style exit code.
    Tests can pass once=True to mount, poll once, and exit headlessly.
    """

    app = DraftgoblinTuiApp(
        log_path=log_path,
        card_database=card_database,
        app_dir=app_dir,
        poll_interval=poll_interval,
        ratings_loader=ratings_loader,
        startup_scan=startup_scan,
        once=once,
        mana_icons_enabled=mana_icons_enabled,
    )
    try:
        app.run(headless=once)
    except KeyboardInterrupt:
        return 130
    except Exception:
        return _ERROR_EXIT_CODE

    return 0


def _recommendation_confidence_label(
    *,
    cards: tuple[ScoredCard, ...],
    ranking_mode: str,
    phase: str,
) -> str | None:
    close_label = _close_pick_label(cards=cards, ranking_mode=ranking_mode)
    if phase == "open":
        if close_label is not None:
            return f"early/open {close_label}; stay flexible"

        return "early/open pick — stay flexible"

    return close_label


def _close_pick_label(
    *,
    cards: tuple[ScoredCard, ...],
    ranking_mode: str,
) -> str | None:
    if len(cards) < 2:
        return None

    top_card, second_card = cards[:2]
    if ranking_mode == "score":
        score_delta = max(0.0, top_card.raw_score - second_card.raw_score)
        if score_delta <= CLOSE_DG_SCORE_THRESHOLD:
            return f"close pick — top two within {score_delta:.1f} DG points"

        return None

    if ranking_mode == "win_rate":
        top_win_rate = top_card.rating.gih_win_rate
        second_win_rate = second_card.rating.gih_win_rate
        if top_win_rate is None or second_win_rate is None:
            return None

        win_rate_delta = max(0.0, top_win_rate - second_win_rate)
        if win_rate_delta <= CLOSE_WIN_RATE_THRESHOLD:
            return f"close pick — top two within {win_rate_delta * 100:.1f}pp WR"

    return None


def _row_cells(
    *,
    rank: int,
    scored_card: ScoredCard,
    column_keys: tuple[str, ...],
    mana_icons_enabled: bool = False,
) -> tuple[object, ...]:
    values = {
        "rank": f"{rank:02d}",
        "win_rate": _format_win_rate(scored_card=scored_card),
        "grade": _format_letter_grade(scored_card=scored_card),
        "score": str(scored_card.score),
        "card": _format_card_name(card=scored_card.card),
        "colors": _styled_colors(
            card=scored_card.card,
            mana_icons_enabled=mana_icons_enabled,
        ),
        "fit": _format_color_fit(scored_card=scored_card),
        "gih": _format_win_rate(scored_card=scored_card),
        "alsa": _format_alsa(scored_card=scored_card),
        "mv": _format_mana_value(card=scored_card.card),
        "source": scored_card.source_label,
    }
    return tuple(values[column_key] for column_key in column_keys)


def _draft_pick_index(*, event: PackOfferedEvent) -> int:
    return (event.pack_number * EXPECTED_PICKS_PER_PACK) + event.pick_number + 1


def _latest_draft_state_sort_key(state: DraftState) -> tuple[str, str, str]:
    return (state.updated_at, state.account_id, state.draft_id)


def _is_missing_account_error(*, event: DraftEvent, error: DraftPoolError) -> bool:
    return (
        str(error) == "Draft event is missing an MTGA account id."
        and _event_is_missing_account(event=event)
    )


def _event_is_missing_account(*, event: DraftEvent) -> bool:
    return (
        isinstance(
            event,
            (DraftStartedEvent, PackOfferedEvent, PickMadeEvent, DraftCompletedEvent),
        )
        and event.account_id is None
    )


def _format_account_label(*, client_id: str, screen_name: str | None) -> str:
    if screen_name is None:
        return client_id

    return screen_name


def _format_card_name(*, card: CardInfo) -> str:
    if card.unknown:
        return f"{card.name} (grpId {card.grp_id})"

    return card.name


def _styled_colors(*, card: CardInfo, mana_icons_enabled: bool = False) -> Text:
    if card.unknown:
        return Text("Unknown", style="bold yellow")

    if not card.colors:
        colorless = _format_colorless_label(
            mana_icons_enabled=mana_icons_enabled,
            long_colorless=False,
        )
        return Text(colorless, style="grey50")

    text = Text()
    for color in card.colors:
        symbol = _format_mana_symbol(
            symbol=color,
            mana_icons_enabled=mana_icons_enabled,
        )
        text.append(symbol, style=COLOR_STYLES.get(color, "bold"))
    return text


def _format_color_fit(*, scored_card: ScoredCard) -> str:
    if scored_card.color_fit == "on-color":
        return "On"

    if scored_card.color_fit == "off-color":
        return "Off!"

    if scored_card.color_fit == "colorless":
        return "Any"

    if scored_card.color_fit == "unknown":
        return "?"

    return "Open"


def _format_win_rate(*, scored_card: ScoredCard) -> str:
    if scored_card.rating.gih_win_rate is None:
        return "—"

    return f"{scored_card.rating.gih_win_rate:.1%}"


def _format_letter_grade(*, scored_card: ScoredCard) -> str:
    return scored_card.rating.letter_grade or "—"


def _format_alsa(*, scored_card: ScoredCard) -> str:
    if scored_card.rating.average_last_seen_at is None:
        return "—"

    return f"{scored_card.rating.average_last_seen_at:.2f}"


def _format_tui_source_label(*, scored_card: ScoredCard) -> str:
    labels = {
        "Quick": "Quick Draft",
        "Premier": "Premier Draft fallback",
        "Prior": "neutral prior",
    }
    return labels.get(scored_card.source_label, scored_card.source_label)


def _format_mana_value(*, card: CardInfo) -> str:
    if card.mana_value is None:
        return "—"

    if card.mana_value.is_integer():
        return str(int(card.mana_value))

    return f"{card.mana_value:.1f}"


def _pool_color_distribution_bar(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
    mana_icons_enabled: bool = False,
) -> str:
    if not pool_grp_ids:
        return "Colors: none"

    counts = _pool_color_counts(
        pool_grp_ids=pool_grp_ids,
        card_database=card_database,
    )
    max_count = max(counts.values(), default=0)
    keys = COLOR_ORDER + (COLORLESS_KEY,)
    parts = []
    for color in keys:
        bar = _scaled_bar(count=counts[color], max_count=max_count)
        label = _format_color_count_label(
            color=color,
            mana_icons_enabled=mana_icons_enabled,
        )
        parts.append(f"{label} {bar} {counts[color]}")
    if counts[UNKNOWN_COLOR_KEY] > 0:
        parts.append(
            f"? {_scaled_bar(count=counts[UNKNOWN_COLOR_KEY], max_count=max_count)} "
            f"{counts[UNKNOWN_COLOR_KEY]}"
        )

    return "Colors: " + " | ".join(parts)


def _pool_color_counts(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
) -> Counter[str]:
    counts: Counter[str] = Counter({color: 0 for color in COLOR_ORDER})
    counts[COLORLESS_KEY] = 0
    counts[UNKNOWN_COLOR_KEY] = 0
    for grp_id in pool_grp_ids:
        card = card_database.lookup(grp_id=grp_id)
        if card.unknown:
            counts[UNKNOWN_COLOR_KEY] += 1
            continue

        if not card.colors:
            counts[COLORLESS_KEY] += 1
            continue

        for color in card.colors:
            if color in COLOR_ORDER:
                counts[color] += 1
            else:
                counts[UNKNOWN_COLOR_KEY] += 1

    return counts


def _pool_curve_sparkline(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
) -> str:
    if not pool_grp_ids:
        return "Curve: none"

    counts = _mana_curve_counts(
        pool_grp_ids=pool_grp_ids,
        card_database=card_database,
    )
    max_count = max(counts, default=0)
    parts = []
    for index, label in enumerate(CURVE_BUCKET_LABELS):
        glyph = _sparkline_glyph(count=counts[index], max_count=max_count)
        parts.append(f"{label}{glyph}{counts[index]}")
    return "Curve: " + " ".join(parts)


def _mana_curve_counts(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
) -> list[int]:
    counts = [0 for _ in CURVE_BUCKET_LABELS]
    for grp_id in pool_grp_ids:
        card = card_database.lookup(grp_id=grp_id)
        if card.unknown or card.mana_value is None or _is_land_card(card=card):
            continue

        bucket = _curve_bucket(mana_value=card.mana_value)
        counts[bucket] += 1

    return counts


def _curve_bucket(*, mana_value: float) -> int:
    rounded = max(0, int(mana_value))
    return min(rounded, len(CURVE_BUCKET_LABELS) - 1)


def _is_land_card(*, card: CardInfo) -> bool:
    return any("Land" in type_line for type_line in card.types)


def _is_creature_card(*, card: CardInfo) -> bool:
    return any("Creature" in type_line for type_line in card.types)


def _scaled_bar(*, count: int, max_count: int, width: int = 5) -> str:
    if count <= 0 or max_count <= 0:
        return "·"

    filled = max(1, round((count / max_count) * width))
    return "█" * filled


def _sparkline_glyph(*, count: int, max_count: int) -> str:
    if count <= 0 or max_count <= 0:
        return "·"

    index = max(0, round((count / max_count) * (len(SPARKLINE_GLYPHS) - 1)))
    return SPARKLINE_GLYPHS[index]
