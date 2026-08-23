"""Adapt shared live-session events to plain-text watch output.
The adapter preserves the established command and output contracts.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from os import PathLike
from typing import TextIO, TypeAlias

from draftgoblin.carddb import CardDatabase
from draftgoblin.config import POLL_INTERVAL_SECONDS
from draftgoblin.deckbuilder import DeckBuilderError, format_build_result
from draftgoblin.events import (
    AccountEvent,
    DraftCompletedEvent,
    DraftStartedEvent,
    PackOfferedEvent,
    PickMadeEvent,
    QuickDraftDetectedEvent,
)
from draftgoblin.replay import (
    format_draft_completed_event,
    format_pack_offered_event,
    format_pick_made_event,
)
from draftgoblin.session import (
    LiveSession,
    LiveSessionEvent,
    OperationKind,
    RequestBuild,
)
from draftgoblin.seventeen import SeventeenLandsData

PathInput: TypeAlias = str | PathLike[str]
RatingsLoader: TypeAlias = Callable[[str], SeventeenLandsData]


class PlainLogWatcher:
    """Render one shared live session as incremental plain text.
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
        self.card_database = card_database
        self.splash_enabled = splash_enabled
        self._events: list[LiveSessionEvent] = []
        self.session = LiveSession(
            log_path=log_path,
            card_database=card_database,
            app_dir=app_dir,
            poll_interval=poll_interval,
            previous_log_path=previous_log_path,
            event_publisher=self._capture_event,
            ratings_loader=ratings_loader,
            lazy_pair_card_ratings=True,
            splash_enabled=splash_enabled,
        )
        self.log_path = self.session.log_path

    def _capture_event(self, published: LiveSessionEvent) -> None:
        if (
            isinstance(published.event, DraftCompletedEvent)
            and published.snapshot.draft is not None
        ):
            snapshot = self.session.dispatch(
                command=RequestBuild(allow_splash=self.splash_enabled),
            )
            published = replace(published, snapshot=snapshot)

        self._events.append(published)

    def poll_once(self) -> str:
        """Process one follower poll cycle and return rendered output.
        Empty strings mean no complete draft events were available this cycle.
        """

        self._events.clear()
        self.session.poll_once()
        return self._render_published_events()

    def scan_startup_files(self, *, include_previous: bool = True) -> str:
        """Process Player-prev.log and Player.log from the beginning.
        This is opt-in recovery for a watch session started mid-draft.
        """

        self._events.clear()
        self.session.scan_startup_files(include_previous=include_previous)
        return self._render_published_events()

    def process_lines(self, *, lines: Iterable[str]) -> str:
        """Pass complete log lines through the shared live session.
        Formatting consumes only published session events and commands.
        """

        self._events.clear()
        self.session.process_lines(lines=lines)
        return self._render_published_events()

    def _render_published_events(self) -> str:
        output_lines: list[str] = []
        for published in self._events:
            output_lines.extend(self._format_event(published=published))

        self._events.clear()
        return _join_output_lines(lines=output_lines)

    def _format_event(self, *, published: LiveSessionEvent) -> list[str]:
        event = published.event
        if isinstance(event, AccountEvent):
            label = _account_label(published=published, account_id=event.client_id)
            if event.previous_client_id is None:
                headline = f"Active account: {label}"
            else:
                previous_label = _account_label(
                    published=published,
                    account_id=event.previous_client_id,
                )
                headline = f"Account switched: {previous_label} -> {label}"

            return [headline, f"Status: active account {label}", ""]

        if isinstance(event, QuickDraftDetectedEvent):
            return []

        if isinstance(event, DraftStartedEvent):
            account_label = _account_label(
                published=published,
                account_id=event.account_id,
            )
            return [
                "Draft started: "
                f"{event.event_name} (set {event.set_code}, draft {event.course_id})",
                f"Status: active account {account_label}, draft {event.course_id}",
                "",
            ]

        if isinstance(event, PackOfferedEvent):
            if published.scored_pack is None:
                raise RuntimeError("Shared live session did not score the offered pack.")

            pack_lines = format_pack_offered_event(
                event=event,
                card_database=self.card_database,
                scored_pack=published.scored_pack,
            )
            account_label = _account_label(
                published=published,
                account_id=event.account_id,
            )
            lines = [
                "Status: "
                f"active account {account_label}, "
                f"pick P{event.pack_number + 1}P{event.pick_number + 1}, "
                f"{_color_status_from_pack_lines(lines=pack_lines)}, "
                f"data {_data_source_from_pack_lines(lines=pack_lines)}"
            ]
            lines.extend(_pack_lines_without_color_status(lines=pack_lines))
            return lines

        if isinstance(event, PickMadeEvent):
            lines = format_pick_made_event(
                event=event,
                card_database=self.card_database,
            )
            lines.append("")
            return lines

        if isinstance(event, DraftCompletedEvent):
            lines = format_draft_completed_event(event=event)
            lines.append("")
            if published.snapshot.draft is not None:
                lines.extend(self._format_build_sheet(published=published))
            return lines

        return []

    def _format_build_sheet(self, *, published: LiveSessionEvent) -> list[str]:
        result = published.snapshot.build
        if result is None:
            error = next(
                (
                    error
                    for error in published.snapshot.errors
                    if error.operation == OperationKind.BUILD
                ),
                None,
            )
            if error is None:
                raise DeckBuilderError("Deck build did not return a result.")

            message = error.message.removeprefix("Deck build failed: ")
            raise DeckBuilderError(message)

        if (
            result.domain_pool is None
            or result.domain_selection is None
            or result.domain_spell_selection is None
            or result.domain_mana_base is None
        ):
            raise DeckBuilderError("Deck build did not include domain details.")

        pool = replace(
            result.domain_pool,
            source_label=(
                f"watch {result.domain_pool.account_id}/"
                f"{result.domain_pool.draft_id}"
            ),
        )
        return format_build_result(
            pool=pool,
            selection=result.domain_selection,
            spell_selection=result.domain_spell_selection,
            mana_base=result.domain_mana_base,
        ).rstrip("\n").splitlines()


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


def _account_label(*, published: LiveSessionEvent, account_id: str | None) -> str:
    if account_id is None:
        return "unknown"

    identity = next(
        (
            account
            for account in published.snapshot.accounts
            if account.account_id == account_id
        ),
        None,
    )
    if identity is None or identity.screen_name is None:
        return account_id

    return f"{identity.screen_name} ({account_id})"


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


def _join_output_lines(*, lines: list[str]) -> str:
    if not lines:
        return ""

    return "\n".join(lines) + "\n"
