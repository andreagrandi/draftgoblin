"""Live plain-text log watching.
Completion events persist the pool and render the build sheet automatically.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from os import PathLike
from pathlib import Path
from typing import TextIO, TypeAlias

from draftgoblin.audit import DraftAuditStore
from draftgoblin.carddb import CardDatabase
from draftgoblin.config import POLL_INTERVAL_SECONDS
from draftgoblin.deckbuilder import BuildPool, build_deck_from_pool, format_build_result
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
from draftgoblin.pickengine import PickEngine
from draftgoblin.pool import DraftPoolError, DraftPoolStore, DraftState
from draftgoblin.ranking import DEFAULT_RANKING_MODE
from draftgoblin.replay import (
    format_draft_completed_event,
    format_pack_offered_event,
    format_pick_made_event,
)
from draftgoblin.seventeen import SeventeenLandsData, SeventeenLandsError

PathInput: TypeAlias = str | PathLike[str]
RatingsLoader: TypeAlias = Callable[[str], SeventeenLandsData]


class PlainLogWatcher:
    """Incremental plain-text watch pipeline.
    Tests can call poll_once to simulate one live polling cycle exactly.
    """

    def __init__(
        self,
        *,
        log_path: PathInput,
        card_database: CardDatabase,
        app_dir: PathInput | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        previous_log_path: PathInput | None = None,
        ratings_loader: RatingsLoader | None = None,
        splash_enabled: bool = True,
    ) -> None:
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
        self.audit_store = DraftAuditStore(app_dir=self.store.root.parent)
        self.ratings_loader = ratings_loader
        self.splash_enabled = splash_enabled
        self._pick_engines_by_set: dict[str, PickEngine] = {}
        self._ratings_data_by_set: dict[str, SeventeenLandsData | None] = {}
        self._account_labels: dict[str, str] = {}
        self._active_account_id: str | None = None

    def poll_once(self) -> str:
        """Process one follower poll cycle and return rendered output.
        Empty strings mean no complete draft events were available this cycle.
        """

        return self.process_lines(lines=self.follower.poll())

    def scan_startup_files(self, *, include_previous: bool = True) -> str:
        """Process Player-prev.log and Player.log from the beginning.
        This is opt-in recovery for a watch session started mid-draft.
        """

        return self.process_lines(
            lines=self.follower.scan_startup_files(include_previous=include_previous)
        )

    def process_lines(self, *, lines: Iterable[str]) -> str:
        """Parse and render a batch of complete raw log lines.
        Pool persistence happens before rendering so conflicts fail loudly.
        """

        output_lines: list[str] = []
        for line in lines:
            events = tuple(self.parser.parse_lines(lines=(line,)))
            self._discard_previous_login_account_context()
            for parsed_event in events:
                event = self._event_with_active_account(event=parsed_event)
                state = self._consume_store_event(event=event)
                if state is not None:
                    self._remember_account_label(
                        client_id=state.account_id,
                        screen_name=state.account_screen_name,
                        replace=False,
                    )
                    if _event_is_missing_account(event=event):
                        event = replace(event, account_id=state.account_id)
                output_lines.extend(self._format_event(event=event, state=state))

        return _join_output_lines(lines=output_lines)

    def _discard_previous_login_account_context(self) -> None:
        if self.parser.login_generation == self._login_generation:
            return

        self._login_generation = self.parser.login_generation
        self._active_account_id = None
        self.store.clear_active_account()

    def _consume_store_event(self, *, event: DraftEvent) -> DraftState | None:
        try:
            return self.store.consume(event=event)
        except DraftPoolError as error:
            if _is_missing_account_error(event=event, error=error):
                return None

            raise

    def _event_with_active_account(self, *, event: DraftEvent) -> DraftEvent:
        if self._active_account_id is None:
            return event

        if not _event_is_missing_account(event=event):
            return event

        return replace(event, account_id=self._active_account_id)

    def _format_event(
        self,
        *,
        event: DraftEvent,
        state: DraftState | None,
    ) -> list[str]:
        if isinstance(event, AccountEvent):
            return self._format_account_event(event=event)

        if isinstance(event, DraftStartedEvent):
            self._active_account_id = event.account_id or self._active_account_id
            if state is not None:
                self.audit_store.record_draft_started(state=state)
            return [
                "Draft started: "
                f"{event.event_name} (set {event.set_code}, draft {event.course_id})",
                "Status: "
                f"active account {self._account_label(account_id=event.account_id)}, "
                f"draft {event.course_id}",
                "",
            ]

        if isinstance(event, PackOfferedEvent):
            self._active_account_id = event.account_id or self._active_account_id
            pick_engine = self._pick_engine_for_set(set_code=event.set_code)
            scored_pack = pick_engine.score_pack(
                offered_grp_ids=event.offered_grp_ids,
                card_database=self.card_database,
                pool_grp_ids=event.pool_grp_ids,
                pick_index=_draft_pick_index(event=event),
            )
            if state is not None:
                self.audit_store.record_decision(
                    state=state,
                    event=event,
                    scored_pack=scored_pack,
                    config=pick_engine.config,
                    ratings_data=pick_engine.ratings_data,
                )
            pack_lines = format_pack_offered_event(
                event=event,
                card_database=self.card_database,
                scored_pack=scored_pack,
            )
            color_status = _color_status_from_pack_lines(lines=pack_lines)
            lines = [
                "Status: "
                f"active account {self._account_label(account_id=event.account_id)}, "
                f"pick P{event.pack_number + 1}P{event.pick_number + 1}, "
                f"{color_status}, "
                f"data {_data_source_from_pack_lines(lines=pack_lines)}"
            ]
            lines.extend(_pack_lines_without_color_status(lines=pack_lines))
            return lines

        if isinstance(event, PickMadeEvent):
            self._active_account_id = event.account_id or self._active_account_id
            if state is not None:
                self.audit_store.record_choice(
                    state=state,
                    event=event,
                    ranking_mode=DEFAULT_RANKING_MODE,
                )
            lines = format_pick_made_event(
                event=event,
                card_database=self.card_database,
            )
            lines.append("")
            return lines

        if isinstance(event, DraftCompletedEvent):
            self._active_account_id = event.account_id or self._active_account_id
            if state is not None:
                self.audit_store.record_draft_completed(state=state, event=event)
            lines = format_draft_completed_event(event=event)
            lines.append("")
            if state is not None:
                lines.extend(self._format_build_sheet(state=state))
            return lines

        return []

    def _format_account_event(self, *, event: AccountEvent) -> list[str]:
        self._remember_account_label(
            client_id=event.client_id,
            screen_name=event.screen_name,
            replace=True,
        )
        self._active_account_id = event.client_id
        label = self._account_label(account_id=event.client_id)

        if event.previous_client_id is None:
            headline = f"Active account: {label}"
        else:
            headline = (
                "Account switched: "
                f"{self._known_account_label(event.previous_client_id)} -> {label}"
            )

        return [
            headline,
            f"Status: active account {label}",
            "",
        ]

    def _account_label(self, *, account_id: str | None) -> str:
        client_id = account_id or self._active_account_id
        if client_id is None:
            return "unknown"

        return self._known_account_label(client_id)

    def _known_account_label(self, client_id: str) -> str:
        return self._account_labels.get(client_id, client_id)

    def _remember_account_label(
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

    def _format_build_sheet(self, *, state: DraftState) -> list[str]:
        pool = BuildPool(
            set_code=state.set_code,
            pool_grp_ids=state.pool_grp_ids,
            source_label=f"watch {state.account_id}/{state.draft_id}",
            account_id=state.account_id,
            draft_id=state.draft_id,
        )
        selection, build_sheet = build_deck_from_pool(
            pool=pool,
            card_database=self.card_database,
            ratings_data=self._ratings_data_for_set(set_code=state.set_code),
            allow_splash=self.splash_enabled,
        )
        return format_build_result(
            pool=pool,
            selection=selection,
            spell_selection=build_sheet.spell_selection,
            mana_base=build_sheet.mana_base,
        ).rstrip("\n").splitlines()

    def _pick_engine_for_set(self, *, set_code: str) -> PickEngine:
        engine = self._pick_engines_by_set.get(set_code)
        if engine is not None:
            return engine

        engine = PickEngine(
            ratings_data=self._ratings_data_for_set(set_code=set_code),
            splash_enabled=self.splash_enabled,
        )
        self._pick_engines_by_set[set_code] = engine
        return engine

    def _ratings_data_for_set(self, *, set_code: str) -> SeventeenLandsData | None:
        if set_code in self._ratings_data_by_set:
            return self._ratings_data_by_set[set_code]

        ratings_data = None
        if self.ratings_loader is not None:
            try:
                ratings_data = self.ratings_loader(set_code)
            except SeventeenLandsError:
                ratings_data = None

        self._ratings_data_by_set[set_code] = ratings_data
        return ratings_data


def run_plain_watch(
    *,
    log_path: PathInput,
    card_database: CardDatabase,
    app_dir: PathInput | None = None,
    output: TextIO | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    once: bool = False,
    startup_scan: bool = False,
    stop_after_empty_polls: int | None = None,
    ratings_loader: RatingsLoader | None = None,
    splash_enabled: bool = True,
) -> int:
    """Run watch --plain until interrupted or a test stop condition fires.
    The process returns zero unless a caller catches and maps an exception.
    """

    if output is None:
        output = sys.stdout

    watcher = PlainLogWatcher(
        log_path=log_path,
        card_database=card_database,
        app_dir=app_dir,
        poll_interval=poll_interval,
        ratings_loader=ratings_loader,
        splash_enabled=splash_enabled,
    )
    output.write("Draftgoblin watch\n")
    output.write(f"Watching: {watcher.log_path}\n")
    output.write("Mode: plain-text\n\n")
    output.flush()

    if startup_scan:
        _write_if_present(output=output, text=watcher.scan_startup_files())

    if once:
        _write_if_present(output=output, text=watcher.poll_once())
        return 0

    empty_polls = 0
    while True:
        text = watcher.poll_once()
        if text:
            _write_if_present(output=output, text=text)
            empty_polls = 0
        else:
            empty_polls += 1

        if stop_after_empty_polls is not None and empty_polls >= stop_after_empty_polls:
            return 0

        time.sleep(poll_interval)


def _write_if_present(*, output: TextIO, text: str) -> None:
    if not text:
        return

    output.write(text)
    output.flush()


def _data_source_from_pack_lines(*, lines: list[str]) -> str:
    for line in lines:
        if line.startswith("Data source: "):
            return line.removeprefix("Data source: ")

    return "unknown"


def _color_status_from_pack_lines(*, lines: list[str]) -> str:
    for line in lines:
        if line.startswith("Status: inferred pair "):
            return line.removeprefix("Status: ")

    return "inferred pair open, commitment 0% (open), pool unknown"


def _pack_lines_without_color_status(*, lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith("Status: inferred pair ")]


def _draft_pick_index(*, event: PackOfferedEvent) -> int:
    return (event.pack_number * EXPECTED_PICKS_PER_PACK) + event.pick_number + 1


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

    return f"{screen_name} ({client_id})"


def _join_output_lines(*, lines: list[str]) -> str:
    if not lines:
        return ""

    return "\n".join(lines) + "\n"
