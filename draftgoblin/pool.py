"""Per-account draft pool state and JSON persistence.
Consume parser events into resumable draft snapshots keyed by account and draft.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any, TypeAlias

from draftgoblin.events import (
    AccountEvent,
    DraftCompletedEvent,
    DraftEvent,
    DraftStartedEvent,
    PackOfferedEvent,
    PickMadeEvent,
)
from draftgoblin.paths import app_data_dir

PathInput: TypeAlias = str | PathLike[str]
Clock: TypeAlias = Callable[[], datetime]

STATE_DIRECTORY_NAME = "state"
STATE_SCHEMA_VERSION = 1


class DraftPoolError(RuntimeError):
    """Raised when persisted draft pool state is invalid or conflicting.
    Callers can surface this as a concise replay or watch diagnostic.
    """


@dataclass(frozen=True, slots=True)
class DraftPick:
    """One draft pick coordinate with offered and chosen card data.
    Offered cards remain optional so pick-only streams can still build pools.
    """

    pack_number: int
    pick_number: int
    offered_grp_ids: tuple[int, ...] | None = None
    pool_before_pick: tuple[int, ...] | None = None
    chosen_grp_id: int | None = None

    @property
    def coordinate(self) -> tuple[int, int]:
        """Return the pack/pick tuple used for idempotent merges.
        Coordinates are unique within one draft state.
        """

        return (self.pack_number, self.pick_number)

    def to_json(self) -> dict[str, object]:
        """Convert this pick to Draftgoblin's state JSON shape.
        Lists are used on disk for stable, readable JSON.
        """

        return {
            "pack_number": self.pack_number,
            "pick_number": self.pick_number,
            "offered_grp_ids": _optional_int_list(self.offered_grp_ids),
            "pool_before_pick": _optional_int_list(self.pool_before_pick),
            "chosen_grp_id": self.chosen_grp_id,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> DraftPick:
        """Load one pick from Draftgoblin's state JSON shape.
        Parsing is strict so corrupted state fails before use.
        """

        return cls(
            pack_number=_required_int(data.get("pack_number"), field_name="pick.pack_number"),
            pick_number=_required_int(data.get("pick_number"), field_name="pick.pick_number"),
            offered_grp_ids=_optional_int_tuple(
                data.get("offered_grp_ids"),
                field_name="pick.offered_grp_ids",
            ),
            pool_before_pick=_optional_int_tuple(
                data.get("pool_before_pick"),
                field_name="pick.pool_before_pick",
            ),
            chosen_grp_id=_optional_int(
                data.get("chosen_grp_id"),
                field_name="pick.chosen_grp_id",
            ),
        )


@dataclass(frozen=True, slots=True)
class DraftState:
    """Persisted state for one account-scoped Quick Draft.
    The pool is the ordered list of chosen Arena grpIds accumulated so far.
    """

    account_id: str
    draft_id: str
    event_name: str
    set_code: str
    course_id: str | None
    started_at: str
    updated_at: str
    completed_at: str | None
    completed: bool
    picks: tuple[DraftPick, ...]
    pool_grp_ids: tuple[int, ...]

    @property
    def chosen_pick_count(self) -> int:
        """Return the number of picks with a chosen card.
        This is the draft pick count represented in the accumulated pool.
        """

        return sum(1 for pick in self.picks if pick.chosen_grp_id is not None)

    def pick_for(self, *, pack_number: int, pick_number: int) -> DraftPick | None:
        """Return an existing pick for the coordinate, if present.
        The state keeps picks sorted but lookup remains explicit and simple.
        """

        for pick in self.picks:
            if pick.pack_number == pack_number and pick.pick_number == pick_number:
                return pick

        return None

    def to_json(self) -> dict[str, object]:
        """Convert this state to Draftgoblin's state JSON shape.
        Keys are intentionally explicit to make persisted drafts inspectable.
        """

        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "account_id": self.account_id,
            "draft_id": self.draft_id,
            "event_name": self.event_name,
            "set_code": self.set_code,
            "course_id": self.course_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "completed": self.completed,
            "picks": [pick.to_json() for pick in self.picks],
            "pool_grp_ids": list(self.pool_grp_ids),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> DraftState:
        """Load a draft state from Draftgoblin's JSON shape.
        Schema mismatches and duplicate coordinates fail loudly.
        """

        schema_version = _required_int(
            data.get("schema_version"),
            field_name="schema_version",
        )
        if schema_version != STATE_SCHEMA_VERSION:
            raise DraftPoolError(
                "Unsupported draft state schema "
                f"{schema_version}; expected {STATE_SCHEMA_VERSION}."
            )

        picks_value = data.get("picks")
        if not isinstance(picks_value, list):
            raise DraftPoolError("Draft state is missing picks list.")

        picks = _sorted_picks(
            picks=tuple(
                _draft_pick_from_value(value=value, index=index)
                for index, value in enumerate(picks_value)
            )
        )
        return cls(
            account_id=_required_str(data.get("account_id"), field_name="account_id"),
            draft_id=_required_str(data.get("draft_id"), field_name="draft_id"),
            event_name=_required_str(data.get("event_name"), field_name="event_name"),
            set_code=_required_str(data.get("set_code"), field_name="set_code"),
            course_id=_optional_str(data.get("course_id"), field_name="course_id"),
            started_at=_required_str(data.get("started_at"), field_name="started_at"),
            updated_at=_required_str(data.get("updated_at"), field_name="updated_at"),
            completed_at=_optional_str(data.get("completed_at"), field_name="completed_at"),
            completed=_required_bool(data.get("completed"), field_name="completed"),
            picks=picks,
            pool_grp_ids=_int_tuple(data.get("pool_grp_ids"), field_name="pool_grp_ids"),
        )


def draft_state_root(*, app_dir: PathInput | None = None) -> Path:
    """Return the root directory for persisted draft state.
    The directory is created by save operations, not path resolution.
    """

    root = Path(app_data_dir() if app_dir is None else app_dir)
    return root / STATE_DIRECTORY_NAME


def draft_state_path(
    *,
    account_id: str,
    draft_id: str,
    app_dir: PathInput | None = None,
) -> Path:
    """Return the JSON path for one account and draft id.
    Account ids map to one subdirectory each under the state root.
    """

    return (
        draft_state_root(app_dir=app_dir)
        / _path_segment(value=account_id, field_name="account_id")
        / f"{_path_segment(value=draft_id, field_name='draft_id')}.json"
    )


def load_draft_state(
    *,
    account_id: str,
    draft_id: str,
    app_dir: PathInput | None = None,
) -> DraftState:
    """Load one persisted draft state by account and draft id.
    Missing or malformed files raise DraftPoolError with context.
    """

    path = draft_state_path(account_id=account_id, draft_id=draft_id, app_dir=app_dir)
    return _load_state_file(path=path)


def list_draft_states(*, app_dir: PathInput | None = None) -> tuple[DraftState, ...]:
    """Load every persisted draft state under the state root.
    States are returned in stable account and filename order.
    """

    root = draft_state_root(app_dir=app_dir)
    if not root.exists():
        return ()

    states: list[DraftState] = []
    for account_dir in sorted(root.iterdir()):
        if account_dir.is_dir():
            states.extend(_load_matching_states(account_dir=account_dir))

    return tuple(states)


def save_draft_state(
    state: DraftState,
    *,
    app_dir: PathInput | None = None,
) -> Path:
    """Persist one draft state atomically and return its path.
    Parent directories are created so each account gets its own folder.
    """

    path = draft_state_path(
        account_id=state.account_id,
        draft_id=state.draft_id,
        app_dir=app_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_json(), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=path.parent,
        encoding="utf-8",
    ) as temporary_file:
        temporary_file.write(payload)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)

    temporary_path.replace(path)
    return path


class DraftPoolStore:
    """Consume parser events into persisted per-account draft states.
    Replaying already-seen events is idempotent and leaves JSON unchanged.
    """

    def __init__(
        self,
        *,
        app_dir: PathInput | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.app_dir = app_dir
        self.root = draft_state_root(app_dir=app_dir)
        self._clock = _system_clock if clock is None else clock
        self._active_account_id: str | None = None
        self._draft_ids_by_event: dict[tuple[str, str], str] = {}

    def consume_all(self, *, events: Iterable[DraftEvent]) -> tuple[DraftState, ...]:
        """Consume an iterable of events and return touched draft states.
        The returned state for each key is the latest state seen in the stream.
        """

        touched: dict[tuple[str, str], DraftState] = {}
        for event in events:
            state = self.consume(event=event)
            if state is not None:
                touched[(state.account_id, state.draft_id)] = state

        return tuple(touched.values())

    def consume(self, *, event: DraftEvent) -> DraftState | None:
        """Consume one parser event and persist any changed draft state.
        Account events only update the active account context.
        """

        if isinstance(event, AccountEvent):
            return self._consume_account(event=event)

        if isinstance(event, DraftStartedEvent):
            return self._consume_started(event=event)

        if isinstance(event, PackOfferedEvent):
            return self._consume_pack_offered(event=event)

        if isinstance(event, PickMadeEvent):
            return self._consume_pick_made(event=event)

        if isinstance(event, DraftCompletedEvent):
            return self._consume_completed(event=event)

        raise DraftPoolError(f"Unsupported draft event {event!r}.")

    def load(self, *, account_id: str, draft_id: str) -> DraftState:
        """Load a draft state from this store's app directory.
        This is a convenience wrapper around load_draft_state.
        """

        return load_draft_state(
            account_id=account_id,
            draft_id=draft_id,
            app_dir=self.app_dir,
        )

    def path_for(self, *, account_id: str, draft_id: str) -> Path:
        """Return the JSON path in this store's app directory.
        The path may not exist until a matching state is saved.
        """

        return draft_state_path(
            account_id=account_id,
            draft_id=draft_id,
            app_dir=self.app_dir,
        )

    def _consume_account(self, *, event: AccountEvent) -> None:
        self._active_account_id = event.client_id
        return None

    def _consume_started(self, *, event: DraftStartedEvent) -> DraftState:
        account_id = self._account_id_for_event(account_id=event.account_id)
        draft_id = event.course_id
        self._remember_draft(
            account_id=account_id,
            event_name=event.event_name,
            draft_id=draft_id,
        )
        state = self._load_existing_state(account_id=account_id, draft_id=draft_id)
        if state is not None:
            _ensure_metadata(
                state=state,
                event_name=event.event_name,
                set_code=event.set_code,
                course_id=event.course_id,
            )
            return state

        now = self._now_iso()
        state = DraftState(
            account_id=account_id,
            draft_id=draft_id,
            event_name=event.event_name,
            set_code=event.set_code,
            course_id=event.course_id,
            started_at=now,
            updated_at=now,
            completed_at=None,
            completed=False,
            picks=(),
            pool_grp_ids=(),
        )
        save_draft_state(state=state, app_dir=self.app_dir)
        return state

    def _consume_pack_offered(self, *, event: PackOfferedEvent) -> DraftState:
        state = self._state_for_draft_event(
            account_id=event.account_id,
            event_name=event.event_name,
            set_code=event.set_code,
        )
        existing_pick = state.pick_for(
            pack_number=event.pack_number,
            pick_number=event.pick_number,
        )
        if existing_pick is None or existing_pick.chosen_grp_id is None:
            _ensure_pool_snapshot(state=state, pool_grp_ids=event.pool_grp_ids)

        merged_pick = _merge_pick(
            existing_pick=existing_pick,
            pack_number=event.pack_number,
            pick_number=event.pick_number,
            offered_grp_ids=event.offered_grp_ids,
            pool_before_pick=event.pool_grp_ids,
            chosen_grp_id=None,
        )
        if existing_pick == merged_pick:
            return state

        updated = replace(
            state,
            picks=_replace_pick(picks=state.picks, pick=merged_pick),
            updated_at=self._now_iso(),
        )
        save_draft_state(state=updated, app_dir=self.app_dir)
        return updated

    def _consume_pick_made(self, *, event: PickMadeEvent) -> DraftState:
        state = self._state_for_draft_event(
            account_id=event.account_id,
            event_name=event.event_name,
            set_code=event.set_code,
        )
        existing_pick = state.pick_for(
            pack_number=event.pack_number,
            pick_number=event.pick_number,
        )
        merged_pick = _merge_pick(
            existing_pick=existing_pick,
            pack_number=event.pack_number,
            pick_number=event.pick_number,
            offered_grp_ids=None,
            pool_before_pick=None,
            chosen_grp_id=event.chosen_grp_id,
        )
        if existing_pick == merged_pick:
            return state

        updated = replace(
            state,
            picks=_replace_pick(picks=state.picks, pick=merged_pick),
            pool_grp_ids=state.pool_grp_ids + (event.chosen_grp_id,),
            updated_at=self._now_iso(),
        )
        save_draft_state(state=updated, app_dir=self.app_dir)
        return updated

    def _consume_completed(self, *, event: DraftCompletedEvent) -> DraftState:
        state = self._state_for_draft_event(
            account_id=event.account_id,
            event_name=event.event_name,
            set_code=event.set_code,
        )
        if state.completed:
            if not _same_pool_contents(state.pool_grp_ids, event.picked_grp_ids):
                raise DraftPoolError(
                    f"Completion for draft {state.draft_id!r} conflicts with saved pool."
                )

            return state

        pool_grp_ids = event.picked_grp_ids if not state.pool_grp_ids else state.pool_grp_ids
        if not _same_pool_contents(pool_grp_ids, event.picked_grp_ids):
            raise DraftPoolError(
                f"Completion for draft {state.draft_id!r} does not match accumulated pool."
            )

        now = self._now_iso()
        updated = replace(
            state,
            completed=True,
            completed_at=now,
            updated_at=now,
            pool_grp_ids=pool_grp_ids,
        )
        save_draft_state(state=updated, app_dir=self.app_dir)
        return updated

    def _state_for_draft_event(
        self,
        *,
        account_id: str | None,
        event_name: str,
        set_code: str,
    ) -> DraftState:
        resolved_account_id = self._account_id_for_draft_event(
            account_id=account_id,
            event_name=event_name,
            set_code=set_code,
        )
        draft_id = self._draft_ids_by_event.get((resolved_account_id, event_name))
        if draft_id is not None:
            state = self._load_existing_state(
                account_id=resolved_account_id,
                draft_id=draft_id,
            )
            if state is None:
                return self._new_event_named_state(
                    account_id=resolved_account_id,
                    event_name=event_name,
                    set_code=set_code,
                    draft_id=draft_id,
                )

            _ensure_metadata(
                state=state,
                event_name=event_name,
                set_code=set_code,
                course_id=state.course_id,
            )
            return state

        resumed = self._find_resume_state(
            account_id=resolved_account_id,
            event_name=event_name,
            set_code=set_code,
        )
        if resumed is not None:
            self._remember_draft(
                account_id=resolved_account_id,
                event_name=event_name,
                draft_id=resumed.draft_id,
            )
            return resumed

        self._remember_draft(
            account_id=resolved_account_id,
            event_name=event_name,
            draft_id=event_name,
        )
        return self._new_event_named_state(
            account_id=resolved_account_id,
            event_name=event_name,
            set_code=set_code,
            draft_id=event_name,
        )

    def _new_event_named_state(
        self,
        *,
        account_id: str,
        event_name: str,
        set_code: str,
        draft_id: str,
    ) -> DraftState:
        now = self._now_iso()
        state = DraftState(
            account_id=account_id,
            draft_id=draft_id,
            event_name=event_name,
            set_code=set_code,
            course_id=None,
            started_at=now,
            updated_at=now,
            completed_at=None,
            completed=False,
            picks=(),
            pool_grp_ids=(),
        )
        save_draft_state(state=state, app_dir=self.app_dir)
        return state

    def _find_resume_state(
        self,
        *,
        account_id: str,
        event_name: str,
        set_code: str,
    ) -> DraftState | None:
        account_dir = self.root / _path_segment(value=account_id, field_name="account_id")
        if not account_dir.exists():
            return None

        matches = [
            state
            for state in _load_matching_states(account_dir=account_dir)
            if state.event_name == event_name and state.set_code == set_code
        ]
        active_matches = [state for state in matches if not state.completed]
        if len(active_matches) == 1:
            return active_matches[0]

        if len(active_matches) > 1:
            raise DraftPoolError(
                f"Multiple active drafts match {event_name!r} for account {account_id!r}."
            )

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            raise DraftPoolError(
                f"Multiple completed drafts match {event_name!r} for account {account_id!r}."
            )

        return None

    def _load_existing_state(self, *, account_id: str, draft_id: str) -> DraftState | None:
        path = draft_state_path(
            account_id=account_id,
            draft_id=draft_id,
            app_dir=self.app_dir,
        )
        if not path.exists():
            return None

        return _load_state_file(path=path)

    def _remember_draft(self, *, account_id: str, event_name: str, draft_id: str) -> None:
        self._draft_ids_by_event[(account_id, event_name)] = draft_id

    def _account_id_for_draft_event(
        self,
        *,
        account_id: str | None,
        event_name: str,
        set_code: str,
    ) -> str:
        if account_id is not None or self._active_account_id is not None:
            return self._account_id_for_event(account_id=account_id)

        inferred_account_id = self._infer_account_id_from_state(
            event_name=event_name,
            set_code=set_code,
        )
        if inferred_account_id is None:
            raise DraftPoolError("Draft event is missing an MTGA account id.")

        self._active_account_id = inferred_account_id
        return inferred_account_id

    def _infer_account_id_from_state(
        self,
        *,
        event_name: str,
        set_code: str,
    ) -> str | None:
        if not self.root.exists():
            return None

        matches = [
            state
            for account_dir in sorted(self.root.iterdir())
            if account_dir.is_dir()
            for state in _load_matching_states(account_dir=account_dir)
            if state.event_name == event_name and state.set_code == set_code
        ]
        active_matches = [state for state in matches if not state.completed]
        if len(active_matches) == 1:
            return active_matches[0].account_id

        if len(active_matches) > 1:
            raise DraftPoolError(f"Multiple active account states match {event_name!r}.")

        if len(matches) == 1:
            return matches[0].account_id

        if len(matches) > 1:
            raise DraftPoolError(f"Multiple account states match {event_name!r}.")

        return None

    def _account_id_for_event(self, *, account_id: str | None) -> str:
        if account_id is not None:
            self._active_account_id = account_id
            return account_id

        if self._active_account_id is None:
            raise DraftPoolError("Draft event is missing an MTGA account id.")

        return self._active_account_id

    def _now_iso(self) -> str:
        return self._clock().astimezone(UTC).isoformat()


def _load_state_file(*, path: Path) -> DraftState:
    if not path.exists():
        raise DraftPoolError(f"Draft state does not exist at {path}.")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DraftPoolError(f"Malformed draft state {path}: {error}.") from error

    if not isinstance(data, dict):
        raise DraftPoolError(f"Malformed draft state {path}: expected object.")

    state = DraftState.from_json(data=data)
    expected_path = draft_state_path(
        account_id=state.account_id,
        draft_id=state.draft_id,
        app_dir=path.parents[2] if len(path.parents) > 2 else None,
    )
    if path.name != expected_path.name:
        raise DraftPoolError(
            f"Draft state {path} filename does not match draft id {state.draft_id!r}."
        )

    return state


def _load_matching_states(*, account_dir: Path) -> tuple[DraftState, ...]:
    states: list[DraftState] = []
    for path in sorted(account_dir.glob("*.json")):
        states.append(_load_state_file(path=path))

    return tuple(states)


def _ensure_metadata(
    *,
    state: DraftState,
    event_name: str,
    set_code: str,
    course_id: str | None,
) -> None:
    if state.event_name != event_name:
        raise DraftPoolError(
            f"Draft {state.draft_id!r} event name changed from "
            f"{state.event_name!r} to {event_name!r}."
        )

    if state.set_code != set_code:
        raise DraftPoolError(
            f"Draft {state.draft_id!r} set changed from {state.set_code!r} to {set_code!r}."
        )

    if course_id is not None and state.course_id not in {None, course_id}:
        raise DraftPoolError(
            f"Draft {state.draft_id!r} course id changed from "
            f"{state.course_id!r} to {course_id!r}."
        )


def _ensure_pool_snapshot(*, state: DraftState, pool_grp_ids: tuple[int, ...]) -> None:
    if not _same_pool_contents(state.pool_grp_ids, pool_grp_ids):
        raise DraftPoolError(
            f"Pack for draft {state.draft_id!r} saw pool {pool_grp_ids!r}, "
            f"but accumulated pool is {state.pool_grp_ids!r}."
        )


def _same_pool_contents(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    return Counter(left) == Counter(right)


def _merge_pick(
    *,
    existing_pick: DraftPick | None,
    pack_number: int,
    pick_number: int,
    offered_grp_ids: tuple[int, ...] | None,
    pool_before_pick: tuple[int, ...] | None,
    chosen_grp_id: int | None,
) -> DraftPick:
    pick = existing_pick or DraftPick(pack_number=pack_number, pick_number=pick_number)
    _ensure_pick_coordinate(
        pick=pick,
        pack_number=pack_number,
        pick_number=pick_number,
    )

    next_offered = _merge_optional_tuple(
        current=pick.offered_grp_ids,
        incoming=offered_grp_ids,
        field_name="offered_grp_ids",
        coordinate=pick.coordinate,
    )
    next_pool_before = _merge_optional_tuple(
        current=pick.pool_before_pick,
        incoming=pool_before_pick,
        field_name="pool_before_pick",
        coordinate=pick.coordinate,
    )
    next_chosen = _merge_optional_int(
        current=pick.chosen_grp_id,
        incoming=chosen_grp_id,
        field_name="chosen_grp_id",
        coordinate=pick.coordinate,
    )
    if next_chosen is not None and next_offered is not None and next_chosen not in next_offered:
        raise DraftPoolError(
            f"Pick {pick.coordinate!r} chose {next_chosen}, which was not offered."
        )

    return replace(
        pick,
        offered_grp_ids=next_offered,
        pool_before_pick=next_pool_before,
        chosen_grp_id=next_chosen,
    )


def _ensure_pick_coordinate(
    *,
    pick: DraftPick,
    pack_number: int,
    pick_number: int,
) -> None:
    if pick.pack_number != pack_number or pick.pick_number != pick_number:
        raise DraftPoolError(
            f"Pick coordinate changed from {pick.coordinate!r} "
            f"to {(pack_number, pick_number)!r}."
        )


def _replace_pick(*, picks: tuple[DraftPick, ...], pick: DraftPick) -> tuple[DraftPick, ...]:
    by_coordinate = {existing.coordinate: existing for existing in picks}
    by_coordinate[pick.coordinate] = pick
    return _sorted_picks(picks=tuple(by_coordinate.values()))


def _sorted_picks(*, picks: tuple[DraftPick, ...]) -> tuple[DraftPick, ...]:
    seen: set[tuple[int, int]] = set()
    for pick in picks:
        if pick.coordinate in seen:
            raise DraftPoolError(f"Duplicate pick coordinate {pick.coordinate!r}.")

        seen.add(pick.coordinate)

    return tuple(sorted(picks, key=lambda pick: pick.coordinate))


def _merge_optional_tuple(
    *,
    current: tuple[int, ...] | None,
    incoming: tuple[int, ...] | None,
    field_name: str,
    coordinate: tuple[int, int],
) -> tuple[int, ...] | None:
    if incoming is None:
        return current

    if current is not None and current != incoming:
        raise DraftPoolError(
            f"Pick {coordinate!r} {field_name} changed from {current!r} to {incoming!r}."
        )

    return incoming


def _merge_optional_int(
    *,
    current: int | None,
    incoming: int | None,
    field_name: str,
    coordinate: tuple[int, int],
) -> int | None:
    if incoming is None:
        return current

    if current is not None and current != incoming:
        raise DraftPoolError(
            f"Pick {coordinate!r} {field_name} changed from {current!r} to {incoming!r}."
        )

    return incoming


def _draft_pick_from_value(*, value: Any, index: int) -> DraftPick:
    if not isinstance(value, dict):
        raise DraftPoolError(f"Draft pick at index {index} is not an object.")

    return DraftPick.from_json(data=value)


def _system_clock() -> datetime:
    return datetime.now(tz=UTC)


def _path_segment(*, value: str, field_name: str) -> str:
    if value in {"", ".", ".."}:
        raise DraftPoolError(f"Invalid {field_name}; cannot be used as a path segment.")

    return value.replace("/", "_").replace("\\", "_").replace(":", "_")


def _optional_int_list(value: tuple[int, ...] | None) -> list[int] | None:
    if value is None:
        return None

    return list(value)


def _required_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise DraftPoolError(f"Missing or invalid {field_name}; expected non-empty string.")

    return value


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None

    return _required_str(value, field_name=field_name)


def _required_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise DraftPoolError(f"Missing or invalid {field_name}; expected boolean.")

    return value


def _required_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise DraftPoolError(f"Missing or invalid {field_name}; expected integer.")

    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise DraftPoolError(f"Missing or invalid {field_name}; expected integer.") from error


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None

    return _required_int(value, field_name=field_name)


def _int_tuple(value: Any, *, field_name: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise DraftPoolError(f"Missing or invalid {field_name}; expected integer list.")

    return tuple(
        _required_int(item, field_name=f"{field_name}[]")
        for item in value
    )


def _optional_int_tuple(value: Any, *, field_name: str) -> tuple[int, ...] | None:
    if value is None:
        return None

    return _int_tuple(value, field_name=field_name)

