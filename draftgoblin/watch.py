"""Live plain-text log watching.
Follow Player.log, parse draft events, persist pools, and render incrementally.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable
from os import PathLike
from pathlib import Path
from typing import TextIO, TypeAlias

from draftgoblin.carddb import CardDatabase
from draftgoblin.config import POLL_INTERVAL_SECONDS
from draftgoblin.events import (
    AccountEvent,
    DraftCompletedEvent,
    DraftEvent,
    DraftLogParser,
    DraftStartedEvent,
    PackOfferedEvent,
    PickMadeEvent,
)
from draftgoblin.logfollow import LogFollower
from draftgoblin.pool import DraftPoolStore
from draftgoblin.replay import (
    format_draft_completed_event,
    format_pack_offered_event,
    format_pick_made_event,
)

PathInput: TypeAlias = str | PathLike[str]


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
        self.store = DraftPoolStore(app_dir=app_dir)
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

        events = tuple(self.parser.parse_lines(lines=lines))
        if not events:
            return ""

        output_lines: list[str] = []
        for event in events:
            self.store.consume(event=event)
            output_lines.extend(self._format_event(event=event))

        return _join_output_lines(lines=output_lines)

    def _format_event(self, *, event: DraftEvent) -> list[str]:
        if isinstance(event, AccountEvent):
            return self._format_account_event(event=event)

        if isinstance(event, DraftStartedEvent):
            self._active_account_id = event.account_id or self._active_account_id
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
            lines = [
                "Status: "
                f"active account {self._account_label(account_id=event.account_id)}, "
                f"pick P{event.pack_number + 1}P{event.pick_number + 1}, "
                f"pool {len(event.pool_grp_ids)}"
            ]
            lines.extend(
                format_pack_offered_event(
                    event=event,
                    card_database=self.card_database,
                )
            )
            return lines

        if isinstance(event, PickMadeEvent):
            self._active_account_id = event.account_id or self._active_account_id
            lines = format_pick_made_event(
                event=event,
                card_database=self.card_database,
            )
            lines.append("")
            return lines

        if isinstance(event, DraftCompletedEvent):
            self._active_account_id = event.account_id or self._active_account_id
            lines = format_draft_completed_event(event=event)
            lines.append("")
            return lines

        return []

    def _format_account_event(self, *, event: AccountEvent) -> list[str]:
        label = _format_account_label(
            client_id=event.client_id,
            screen_name=event.screen_name,
        )
        self._account_labels[event.client_id] = label
        self._active_account_id = event.client_id

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


def _format_account_label(*, client_id: str, screen_name: str | None) -> str:
    if screen_name is None:
        return client_id

    return f"{screen_name} ({client_id})"


def _join_output_lines(*, lines: list[str]) -> str:
    if not lines:
        return ""

    return "\n".join(lines) + "\n"
