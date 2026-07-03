"""Deterministic plain-text replay rendering.
Wire parsed draft events through pool validation and card metadata lookup.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TypeAlias

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.events import (
    AccountEvent,
    DraftCompletedEvent,
    DraftEvent,
    DraftStartedEvent,
    PackOfferedEvent,
    PickMadeEvent,
    parse_events,
)
from draftgoblin.pool import DraftPoolStore

PathInput: TypeAlias = str | PathLike[str]


class ReplayError(RuntimeError):
    """Raised when a log cannot be replayed.
    Callers should surface the message as a concise CLI diagnostic.
    """


@dataclass(frozen=True, slots=True)
class _ReplayHeader:
    """Summary fields printed before replayed picks.
    Missing fields are rendered explicitly so output stays deterministic.
    """

    account_id: str | None
    screen_name: str | None
    event_name: str | None
    set_code: str | None
    draft_id: str | None


def replay_log_file(*, logfile: PathInput, card_database: CardDatabase) -> str:
    """Replay one captured Player.log file into deterministic text.
    The command path performs no network I/O and reads card data from callers.
    """

    path = Path(logfile)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ReplayError(f"Could not read replay log {path}: {error}.") from error

    events = tuple(parse_events(lines=lines))
    return render_replay_events(events=events, card_database=card_database)


def render_replay_events(
    *,
    events: Iterable[DraftEvent],
    card_database: CardDatabase,
) -> str:
    """Render parsed events to stable plain-text replay output.
    Pool validation is run first so conflicting streams fail before printing.
    """

    event_tuple = tuple(events)
    if not event_tuple:
        raise ReplayError("No Quick Draft events found in log file.")

    _validate_events_with_pool(events=event_tuple)

    header = _header_from_events(events=event_tuple)
    lines = _format_header(header=header)
    lines.append("")

    for event in event_tuple:
        if isinstance(event, PackOfferedEvent):
            lines.extend(
                format_pack_offered_event(
                    event=event,
                    card_database=card_database,
                )
            )
        elif isinstance(event, PickMadeEvent):
            lines.extend(
                format_pick_made_event(
                    event=event,
                    card_database=card_database,
                )
            )
            lines.append("")
        elif isinstance(event, DraftCompletedEvent):
            lines.extend(format_draft_completed_event(event=event))

    return "\n".join(lines).rstrip() + "\n"


def _validate_events_with_pool(*, events: tuple[DraftEvent, ...]) -> None:
    with tempfile.TemporaryDirectory(prefix="draftgoblin-replay-") as temporary_dir:
        store = DraftPoolStore(app_dir=temporary_dir)
        store.consume_all(events=events)


def _header_from_events(*, events: tuple[DraftEvent, ...]) -> _ReplayHeader:
    account_names: dict[str, str | None] = {}
    active_account_id: str | None = None
    event_name: str | None = None
    set_code: str | None = None
    draft_id: str | None = None

    for event in events:
        if isinstance(event, AccountEvent):
            active_account_id = event.client_id
            account_names[event.client_id] = event.screen_name
            continue

        if isinstance(event, DraftStartedEvent):
            active_account_id = event.account_id or active_account_id
            event_name = event.event_name
            set_code = event.set_code
            draft_id = event.course_id
            break

        if isinstance(event, (PackOfferedEvent, PickMadeEvent, DraftCompletedEvent)):
            active_account_id = event.account_id or active_account_id
            event_name = event.event_name
            set_code = event.set_code
            draft_id = event.event_name
            break

    screen_name = None
    if active_account_id is not None:
        screen_name = account_names.get(active_account_id)

    return _ReplayHeader(
        account_id=active_account_id,
        screen_name=screen_name,
        event_name=event_name,
        set_code=set_code,
        draft_id=draft_id,
    )


def format_pack_offered_event(
    *,
    event: PackOfferedEvent,
    card_database: CardDatabase,
) -> list[str]:
    """Format a pack offer with the same plain text replay uses.
    Live watch mode calls this so pack rendering stays byte-compatible.
    """

    return _format_pack(event=event, card_database=card_database)


def format_pick_made_event(
    *,
    event: PickMadeEvent,
    card_database: CardDatabase,
) -> list[str]:
    """Format a chosen-card event with replay-compatible text.
    The caller decides whether to add a separating blank line.
    """

    return [
        "Chosen card: "
        f"{format_card_info(card_database.lookup(grp_id=event.chosen_grp_id))}"
    ]


def format_draft_completed_event(*, event: DraftCompletedEvent) -> list[str]:
    """Format draft completion with replay-compatible text.
    Completion type records whether Arena emitted an explicit status.
    """

    completion_type = "inferred" if event.inferred else "explicit"
    return [
        "Draft complete: "
        f"{len(event.picked_grp_ids)} cards ({completion_type} completion)"
    ]


def format_card_info(card: CardInfo) -> str:
    """Format one card for plain CLI output.
    Unknown cards are displayed explicitly instead of failing lookups.
    """

    return _format_card(card)


def _format_header(*, header: _ReplayHeader) -> list[str]:
    return [
        "Draftgoblin replay",
        f"Account: {_format_account(header=header)}",
        f"Set: {header.set_code or 'unknown'}",
        f"Event: {header.event_name or 'unknown'}",
        f"Draft: {header.draft_id or 'unknown'}",
    ]


def _format_account(*, header: _ReplayHeader) -> str:
    if header.account_id is None:
        return "unknown"

    if header.screen_name is None:
        return header.account_id

    return f"{header.screen_name} ({header.account_id})"


def _format_pack(
    *,
    event: PackOfferedEvent,
    card_database: CardDatabase,
) -> list[str]:
    lines = [
        f"Pack {event.pack_number + 1} Pick {event.pick_number + 1}",
        "Offered cards:",
    ]
    for index, grp_id in enumerate(event.offered_grp_ids, start=1):
        card = card_database.lookup(grp_id=grp_id)
        lines.append(f"  {index:02d}. {_format_card(card)}")

    return lines


def _format_card(card: CardInfo) -> str:
    if card.unknown:
        colors = "Unknown"
    else:
        colors = "".join(card.colors) if card.colors else "Colorless"

    return f"{card.name} [{colors}] (grpId {card.grp_id})"
