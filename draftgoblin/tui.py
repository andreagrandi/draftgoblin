"""Textual live interface for Draftgoblin watch mode.
Render score-sorted packs and status updates without blocking fetches.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from os import PathLike
from pathlib import Path
from typing import TypeAlias

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static
from textual.worker import get_current_worker

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import POLL_INTERVAL_SECONDS
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
from draftgoblin.pool import DraftPoolStore, DraftState
from draftgoblin.seventeen import SEVENTEEN_LANDS_ATTRIBUTION, SeventeenLandsData

PathInput: TypeAlias = str | PathLike[str]
RatingsLoader: TypeAlias = Callable[[str], SeventeenLandsData]

PRIMARY_COLUMN_KEYS = ("rank", "score", "card", "colors")
SECONDARY_COLUMN_KEYS = ("fit", "gih", "alsa", "mv", "source")
SORT_MODES = ("score", "alsa", "mv")
SECONDARY_COLUMN_MIN_WIDTH = 88
SIDEBAR_MIN_WIDTH = 56

COLOR_STYLES = {
    "W": "bold bright_white",
    "U": "bold dodger_blue1",
    "B": "bold grey50",
    "R": "bold red3",
    "G": "bold green3",
}

COLUMN_LABELS = {
    "rank": "#",
    "score": "Score",
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
    "score": 7,
    "card": None,
    "colors": 10,
    "fit": 6,
    "gih": 8,
    "alsa": 7,
    "mv": 5,
    "source": 9,
}

SORT_LABELS = {
    "score": "Score",
    "alsa": "ALSA",
    "mv": "MV",
}


class DraftgoblinTuiApp(App[None]):
    """Textual app for live Quick Draft recommendations.
    The app can tail a real log or accept fixture lines in tests.
    """

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

    #pack-table {
        height: 1fr;
    }

    #pool-summary,
    #last-picks {
        height: auto;
        margin-bottom: 1;
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
        Binding("s", "cycle_sort", "Sort", show=True),
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
        self.store = DraftPoolStore(app_dir=app_dir)
        self.ratings_loader = ratings_loader
        self.startup_scan = startup_scan
        self.once = once
        self.poll_enabled = poll_enabled
        self.poll_interval = poll_interval

        self.show_secondary_columns = True
        self.sort_mode = "score"
        self._visible_column_keys: tuple[str, ...] = ()
        self._account_labels: dict[str, str] = {}
        self._active_account_id: str | None = None
        self._active_account_label = "unknown"
        self._event_name: str | None = None
        self._set_code: str | None = None
        self._draft_id: str | None = None
        self._pick_label = "—"
        self._pool_size = 0
        self._pair_label = "open"
        self._commitment_label = "0% open"
        self._data_source = "unknown"
        self._last_error: str | None = None
        self._last_picks: list[str] = []
        self._current_pack_event: PackOfferedEvent | None = None
        self._current_pack: ScoredPack | None = None
        self._ratings_data_by_set: dict[str, SeventeenLandsData | None] = {}
        self._rating_errors_by_set: dict[str, str] = {}
        self._loading_rating_sets: set[str] = set()

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

    def compose(self) -> ComposeResult:
        """Compose the pack table, sidebar, status bar, and key footer.
        Textual's Footer automatically renders the declared keybindings.
        """

        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="pack-panel"):
                yield Static("Waiting for a Quick Draft pack…", id="pack-title")
                yield DataTable(id="pack-table")
            with Vertical(id="sidebar"):
                yield Static("Pool: no draft yet", id="pool-summary")
                yield Static("Last picks: none", id="last-picks")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Prepare widgets and start live log polling.
        The first poll is scheduled as a worker so file I/O does not block UI.
        """

        table = self.query_one("#pack-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        self._render_all()

        if not self.poll_enabled:
            return

        if self.startup_scan:
            self._scan_startup_files_worker()

        self._poll_log_worker(exit_after=self.once)
        if not self.once:
            self.set_interval(self.poll_interval, self._poll_log_worker)

    def on_resize(self, event: events.Resize) -> None:
        """Rebuild the table when width crosses a degradation threshold.
        Secondary columns are hidden first on narrow terminals.
        """

        self._render_all()

    def action_toggle_secondary_columns(self) -> None:
        """Toggle secondary pack-stat columns.
        Narrow terminals still hide them until enough width is available.
        """

        self.show_secondary_columns = not self.show_secondary_columns
        self._render_all()

    def action_cycle_sort(self) -> None:
        """Cycle pack sorting through score, ALSA, and mana value.
        The table is rebuilt from the last scored pack on every change.
        """

        index = SORT_MODES.index(self.sort_mode)
        self.sort_mode = SORT_MODES[(index + 1) % len(SORT_MODES)]
        self._render_all()

    def process_lines(self, *, lines: Iterable[str]) -> None:
        """Process complete Player.log lines and refresh the TUI.
        Tests call this directly to simulate a live fixture stream.
        """

        try:
            events_tuple = tuple(self.parser.parse_lines(lines=lines))
            for event in events_tuple:
                state = self.store.consume(event=event)
                self._consume_event(event=event, state=state)
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
    def _scan_startup_files_worker(self) -> None:
        """Scan startup recovery files in a worker thread.
        Startup scans may read Player-prev.log and the current Player.log.
        """

        try:
            lines = tuple(self.follower.scan_startup_files(include_previous=True))
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.call_from_thread(self._record_error, str(error))
            return

        self.call_from_thread(self.process_lines, lines=lines)

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
        label = _format_account_label(
            client_id=event.client_id,
            screen_name=event.screen_name,
        )
        self._account_labels[event.client_id] = label
        self._active_account_id = event.client_id
        self._active_account_label = label

    def _consume_started_event(self, *, event: DraftStartedEvent) -> None:
        self._active_account_id = event.account_id or self._active_account_id
        self._active_account_label = self._account_label(account_id=event.account_id)
        self._event_name = event.event_name
        self._set_code = event.set_code
        self._draft_id = event.course_id
        self._pick_label = "waiting"
        self._pool_size = 0
        self._ensure_ratings_load_started(set_code=event.set_code)

    def _consume_pack_event(self, *, event: PackOfferedEvent) -> None:
        self._active_account_id = event.account_id or self._active_account_id
        self._active_account_label = self._account_label(account_id=event.account_id)
        self._event_name = event.event_name
        self._set_code = event.set_code
        self._pick_label = f"P{event.pack_number + 1}P{event.pick_number + 1}"
        self._pool_size = len(event.pool_grp_ids)
        self._current_pack_event = event
        self._ensure_ratings_load_started(set_code=event.set_code)
        self._score_current_pack()

    def _consume_pick_event(
        self,
        *,
        event: PickMadeEvent,
        state: DraftState | None,
    ) -> None:
        self._active_account_id = event.account_id or self._active_account_id
        self._active_account_label = self._account_label(account_id=event.account_id)
        self._pick_label = f"P{event.pack_number + 1}P{event.pick_number + 1} picked"
        if state is not None:
            self._pool_size = len(state.pool_grp_ids)

        card = self.card_database.lookup(grp_id=event.chosen_grp_id)
        self._last_picks.append(_format_card_name(card=card))
        self._last_picks = self._last_picks[-5:]

    def _consume_completed_event(
        self,
        *,
        event: DraftCompletedEvent,
        state: DraftState | None,
    ) -> None:
        self._active_account_id = event.account_id or self._active_account_id
        self._active_account_label = self._account_label(account_id=event.account_id)
        self._event_name = event.event_name
        self._set_code = event.set_code
        self._pick_label = "complete"
        self._pool_size = len(event.picked_grp_ids if state is None else state.pool_grp_ids)
        self._current_pack_event = None
        self._current_pack = None

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

        self._render_all()

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
        self._render_status_bar()

    def _update_responsive_visibility(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar.display = self.size.width >= SIDEBAR_MIN_WIDTH

    def _render_pack_title(self) -> None:
        title = self.query_one("#pack-title", Static)
        if self._current_pack_event is None:
            if self._pick_label == "complete":
                title.update("Draft complete")
            else:
                title.update("Waiting for a Quick Draft pack…")
            return

        event = self._current_pack_event
        title.update(
            "Pack "
            f"{event.pack_number + 1} "
            "Pick "
            f"{event.pick_number + 1} "
            f"— sort {SORT_LABELS[self.sort_mode]}"
        )

    def _render_pack_table(self) -> None:
        table = self.query_one("#pack-table", DataTable)
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
            )
            table.add_row(
                *row,
                key=f"{rank}-{scored_card.card.grp_id}-{scored_card.original_index}",
            )

    def _render_sidebar(self) -> None:
        pool_summary = self.query_one("#pool-summary", Static)
        last_picks = self.query_one("#last-picks", Static)
        event_text = self._event_name or "unknown event"
        draft_text = self._draft_id or "unknown draft"
        pool_summary.update(
            "Pool summary\n"
            f"Set: {self._set_code or 'unknown'}\n"
            f"Event: {event_text}\n"
            f"Draft: {draft_text}\n"
            f"Pool size: {self._pool_size}\n"
            f"Inferred pair: {self._pair_label}"
        )
        if not self._last_picks:
            last_picks.update("Last picks: none")
            return

        last_picks.update(
            "Last picks:\n" + "\n".join(f"• {pick}" for pick in self._last_picks)
        )

    def _render_status_bar(self) -> None:
        status = self.query_one("#status-bar", Static)
        text = (
            f"Account: {self._active_account_label} | "
            f"Pair: {self._pair_label} ({self._commitment_label}) | "
            f"Pick: {self._pick_label} | "
            f"Pool: {self._pool_size} | "
            f"Data: {self._data_source} | "
            f"Sort: {SORT_LABELS[self.sort_mode]} | "
            f"{SEVENTEEN_LANDS_ATTRIBUTION}"
        )
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

        if self.sort_mode == "alsa":
            return tuple(sorted(self._current_pack.cards, key=_alsa_sort_key))

        if self.sort_mode == "mv":
            return tuple(sorted(self._current_pack.cards, key=_mana_value_sort_key))

        return self._current_pack.cards

    def _account_label(self, *, account_id: str | None) -> str:
        client_id = account_id or self._active_account_id
        if client_id is None:
            return "unknown"

        return self._account_labels.get(client_id, client_id)

    def _record_error(self, message: str) -> None:
        self._last_error = message
        self._render_all()


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
    )
    try:
        app.run(headless=once)
    except KeyboardInterrupt:
        return 130
    except Exception:
        return _ERROR_EXIT_CODE

    return 0


def _row_cells(
    *,
    rank: int,
    scored_card: ScoredCard,
    column_keys: tuple[str, ...],
) -> tuple[object, ...]:
    values = {
        "rank": f"{rank:02d}",
        "score": str(scored_card.score),
        "card": _format_card_name(card=scored_card.card),
        "colors": _styled_colors(card=scored_card.card),
        "fit": _format_color_fit(scored_card=scored_card),
        "gih": _format_win_rate(scored_card=scored_card),
        "alsa": _format_alsa(scored_card=scored_card),
        "mv": _format_mana_value(card=scored_card.card),
        "source": scored_card.source_label,
    }
    return tuple(values[column_key] for column_key in column_keys)


def _draft_pick_index(*, event: PackOfferedEvent) -> int:
    return (event.pack_number * EXPECTED_PICKS_PER_PACK) + event.pick_number + 1


def _format_account_label(*, client_id: str, screen_name: str | None) -> str:
    if screen_name is None:
        return client_id

    return f"{screen_name} ({client_id})"


def _format_card_name(*, card: CardInfo) -> str:
    if card.unknown:
        return f"{card.name} (grpId {card.grp_id})"

    return card.name


def _styled_colors(*, card: CardInfo) -> Text:
    if card.unknown:
        return Text("Unknown", style="bold yellow")

    if not card.colors:
        return Text("Colorless", style="grey50")

    text = Text()
    for color in card.colors:
        text.append(color, style=COLOR_STYLES.get(color, "bold"))
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


def _format_alsa(*, scored_card: ScoredCard) -> str:
    if scored_card.rating.average_last_seen_at is None:
        return "—"

    return f"{scored_card.rating.average_last_seen_at:.2f}"


def _format_mana_value(*, card: CardInfo) -> str:
    if card.mana_value is None:
        return "—"

    if card.mana_value.is_integer():
        return str(int(card.mana_value))

    return f"{card.mana_value:.1f}"


def _alsa_sort_key(card: ScoredCard) -> tuple[float, int, int]:
    alsa = card.rating.average_last_seen_at
    sort_alsa = float("inf") if alsa is None else alsa
    return (sort_alsa, -card.score, card.original_index)


def _mana_value_sort_key(card: ScoredCard) -> tuple[float, int, int]:
    mana_value = card.card.mana_value
    sort_mana_value = float("inf") if mana_value is None else mana_value
    return (sort_mana_value, -card.score, card.original_index)
