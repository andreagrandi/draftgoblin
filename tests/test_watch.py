from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from draftgoblin.audit import load_draft_audit_records
from draftgoblin.carddb import CardDatabase, CardInfo, build_card_database_from_bulk_file
from draftgoblin.pool import draft_state_path, load_draft_state
from draftgoblin.seventeen import (
    QUICK_DRAFT_FORMAT,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsError,
    SeventeenLandsFormatData,
)
from draftgoblin.watch import PlainLogWatcher

FIXTURE_LOG_PATH = Path(__file__).parent / "fixtures" / "quick-draft-msh-player.log"
SCRYFALL_BULK_SAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "scryfall-default-cards-sample.jsonl"
)
FIXTURE_ACCOUNT_ID = "FIXTURECLIENTID1234567890"
FIXTURE_DRAFT_ID = "00000000-0000-4000-8000-000000000004"


def test_plain_watch_processes_appended_lines_incrementally(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    log_path.write_text("", encoding="utf-8")
    watcher = PlainLogWatcher(
        log_path=log_path,
        app_dir=app_dir,
        card_database=_fixture_card_database(),
        poll_interval=0.01,
    )
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()

    assert watcher.poll_once() == ""

    _append_lines(path=log_path, lines=fixture_lines[:7])
    pack_output = watcher.poll_once()

    assert pack_output.startswith(
        "Active account: FixturePlayer (FIXTURECLIENTID1234567890)\n"
        "Status: active account FixturePlayer (FIXTURECLIENTID1234567890)\n\n"
        "Draft started: QuickDraft_MSH_20260702 "
        "(set MSH, draft 00000000-0000-4000-8000-000000000004)\n"
    )
    assert "Active account: FixturePlayer (FIXTURECLIENTID1234567890)" in pack_output
    assert "Draft started: QuickDraft_MSH_20260702" in pack_output
    assert "Status: active account FixturePlayer" in pack_output
    assert "data neutral prior" in pack_output
    assert "Pack 1 Pick 1" in pack_output
    assert "Fixture Spider (grpId 105097)" in pack_output
    assert "Chosen card:" not in pack_output
    assert pack_output.index("Status: active account FixturePlayer") < (
        pack_output.index("Pack 1 Pick 1")
    )
    assert pack_output.index("Pack 1 Pick 1") < pack_output.index("Data source: ")
    assert pack_output.index("Data source: ") < pack_output.index("Offered cards:")

    _append_lines(path=log_path, lines=fixture_lines[7:8])
    pick_output = watcher.poll_once()

    assert pick_output == "Chosen card: Fixture Spider [G] (grpId 105097)\n\n"
    assert watcher.poll_once() == ""


def test_plain_watch_renders_accountless_draft_event_without_crashing(
    tmp_path: Path,
) -> None:
    watcher = PlainLogWatcher(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        poll_interval=0.01,
    )

    output = watcher.process_lines(
        lines=[
            _course_line(
                event_name="QuickDraft_MSH_20260703",
                course_id="new-draft",
            )
        ]
    )

    assert "Draft started: QuickDraft_MSH_20260703" in output
    assert "Status: active account unknown, draft new-draft" in output


def test_plain_watch_scores_accountless_pack_through_shared_session(
    tmp_path: Path,
) -> None:
    watcher = PlainLogWatcher(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        poll_interval=0.01,
    )

    output = watcher.process_lines(
        lines=[
            _pack_line(
                event_name="QuickDraft_MSH_20260703",
                pack_number=0,
                pick_number=0,
                draft_pack=(105097, 104894),
                picked_cards=(),
            )
        ]
    )

    assert "Status: active account unknown, pick P1P1" in output
    assert "Pack 1 Pick 1" in output
    assert "Fixture Spider (grpId 105097)" in output


def test_plain_watch_does_not_assign_post_login_draft_events_to_prior_account(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    watcher = PlainLogWatcher(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_fixture_card_database(),
        poll_interval=0.01,
    )

    output = watcher.process_lines(
        lines=[
            _auth_line(client_id="first-account", screen_name="First"),
            "[Accounts - Login] Logged in successfully. "
            "Display Name: Second#12345",
            _course_line(
                event_name="QuickDraft_MSH_20260703",
                course_id="second-draft",
            ),
        ]
    )

    assert "Status: active account unknown, draft second-draft" in output
    assert not draft_state_path(
        account_id="first-account",
        draft_id="second-draft",
        app_dir=app_dir,
    ).exists()


def test_plain_watch_degrades_to_neutral_when_ratings_loader_fails(
    tmp_path: Path,
) -> None:
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    watcher = PlainLogWatcher(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        poll_interval=0.01,
        ratings_loader=_failing_ratings_loader,
    )

    output = watcher.process_lines(lines=fixture_lines[:7])

    assert "Status: active account FixturePlayer" in output
    assert "data neutral prior" in output
    assert "Fixture Spider (grpId 105097)" in output
    assert "Prior*" in output


def test_plain_watch_loads_locked_pair_ratings_through_shared_session(
    tmp_path: Path,
) -> None:
    pair_loads: list[str] = []
    ratings_data = _lazy_pair_ratings_data(pair_loads=pair_loads)
    watcher = PlainLogWatcher(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_pair_ratings_card_database(),
        poll_interval=0.01,
        ratings_loader=lambda set_code: ratings_data,
    )

    output = watcher.process_lines(
        lines=[
            _pack_line(
                event_name="QuickDraft_TST_20260703",
                pack_number=1,
                pick_number=1,
                draft_pack=(3, 4),
                picked_cards=(1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1),
            )
        ]
    )

    assert pair_loads == ["WU"]
    assert "commitment 100% (locked)" in output
    assert "80.0%" in output
    assert output.index("Pair Upgrade (grpId 3)") < output.index(
        "All-Decks Leader (grpId 4)"
    )


def test_plain_watch_recovers_rotation_tail_without_loss_or_duplication(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    previous_log_path = tmp_path / "Player-prev.log"
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    watcher = PlainLogWatcher(
        log_path=log_path,
        app_dir=app_dir,
        card_database=_fixture_card_database(),
        poll_interval=0.01,
    )

    _write_lines(path=log_path, lines=fixture_lines[:8])
    output = watcher.poll_once()
    _append_lines(path=log_path, lines=fixture_lines[8:20])
    log_path.rename(previous_log_path)
    _write_lines(path=log_path, lines=fixture_lines[20:])

    output += watcher.poll_once()
    output += watcher.poll_once()

    assert _line_count(output=output, prefix="Pack ") == 42
    assert output.count("Chosen card:") == 42
    assert output.count("Draft complete: 42 cards (explicit completion)") == 1

    state = load_draft_state(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    )
    assert state.completed is True
    assert state.chosen_pick_count == 42
    assert len(state.pool_grp_ids) == 42
    audit_records = load_draft_audit_records(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    )
    assert len(audit_records) == 86
    assert sum(
        record["record_type"] == "decision_evaluated" for record in audit_records
    ) == 42
    assert sum(record["record_type"] == "choice_made" for record in audit_records) == 42
    assert audit_records[-1]["record_type"] == "draft_completed"


def test_plain_watch_account_switch_announces_and_separates_state(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    watcher = PlainLogWatcher(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_small_card_database(),
        poll_interval=0.01,
    )

    output = watcher.process_lines(lines=_two_account_log_lines())

    assert "Active account: First (ACCOUNT-A)" in output
    assert "Account switched: First (ACCOUNT-A) -> Second (ACCOUNT-B)" in output
    assert "Status: active account Second (ACCOUNT-B), pick P1P1" in output
    assert "inferred pair open, commitment 0% (open), pool 0" in output
    assert output.count("Pool: watch ACCOUNT-A/draft-a") == 1
    assert output.count("Pool: watch ACCOUNT-B/draft-b") == 1
    assert output.index("Pool: watch ACCOUNT-A/draft-a") < output.index(
        "Account switched: First (ACCOUNT-A) -> Second (ACCOUNT-B)"
    )
    assert output.index("Account switched: First (ACCOUNT-A) -> Second (ACCOUNT-B)") < (
        output.index("Pool: watch ACCOUNT-B/draft-b")
    )

    first_state = load_draft_state(
        account_id="ACCOUNT-A",
        draft_id="draft-a",
        app_dir=app_dir,
    )
    second_state = load_draft_state(
        account_id="ACCOUNT-B",
        draft_id="draft-b",
        app_dir=app_dir,
    )
    assert first_state.pool_grp_ids == (101,)
    assert second_state.pool_grp_ids == (201,)
    assert first_state.completed is True
    assert second_state.completed is True


def _fixture_card_database() -> CardDatabase:
    return build_card_database_from_bulk_file(path=SCRYFALL_BULK_SAMPLE_PATH)


def _failing_ratings_loader(set_code: str) -> NoReturn:
    raise SeventeenLandsError(f"ratings unavailable for {set_code}")


def _small_card_database() -> CardDatabase:
    return CardDatabase(
        cards={
            101: CardInfo(
                grp_id=101,
                name="First Account Pick",
                colors=("G",),
                mana_value=2.0,
                rarity="common",
                types=("Creature",),
            ),
            102: CardInfo(
                grp_id=102,
                name="First Account Other",
                colors=("U",),
                mana_value=3.0,
                rarity="common",
                types=("Instant",),
            ),
            201: CardInfo(
                grp_id=201,
                name="Second Account Pick",
                colors=("R",),
                mana_value=2.0,
                rarity="common",
                types=("Creature",),
            ),
            202: CardInfo(
                grp_id=202,
                name="Second Account Other",
                colors=("W",),
                mana_value=3.0,
                rarity="common",
                types=("Sorcery",),
            ),
        }
    )


def _pair_ratings_card_database() -> CardDatabase:
    return CardDatabase(
        cards={
            1: CardInfo(
                grp_id=1,
                name="White Pool Card",
                colors=("W",),
                mana_value=2.0,
                rarity="common",
                types=("Creature",),
            ),
            2: CardInfo(
                grp_id=2,
                name="Blue Pool Card",
                colors=("U",),
                mana_value=2.0,
                rarity="common",
                types=("Creature",),
            ),
            3: CardInfo(
                grp_id=3,
                name="Pair Upgrade",
                colors=("W",),
                mana_value=3.0,
                rarity="common",
                types=("Creature",),
            ),
            4: CardInfo(
                grp_id=4,
                name="All-Decks Leader",
                colors=("U",),
                mana_value=3.0,
                rarity="common",
                types=("Creature",),
            ),
        }
    )


def _lazy_pair_ratings_data(*, pair_loads: list[str]) -> SeventeenLandsData:
    fetched_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    pair_data = SeventeenLandsFormatData(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        fetched_at=fetched_at,
        card_ratings={
            3: _ratings_stats(grp_id=3, name="Pair Upgrade", win_rate=0.80),
            4: _ratings_stats(grp_id=4, name="All-Decks Leader", win_rate=0.60),
        },
        pair_win_rates={},
    )

    def load_pair(pair: str) -> SeventeenLandsFormatData:
        pair_loads.append(pair)
        return pair_data

    return SeventeenLandsData(
        set_code="TST",
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code="TST",
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=fetched_at,
            card_ratings={
                3: _ratings_stats(grp_id=3, name="Pair Upgrade", win_rate=0.50),
                4: _ratings_stats(
                    grp_id=4,
                    name="All-Decks Leader",
                    win_rate=0.65,
                ),
            },
            pair_win_rates={},
        ),
        fallback=None,
        pair_card_ratings_loader=load_pair,
    )


def _ratings_stats(*, grp_id: int, name: str, win_rate: float) -> SeventeenCardStats:
    return SeventeenCardStats(
        grp_id=grp_id,
        name=name,
        color="",
        rarity="common",
        average_last_seen_at=3.0,
        gih_win_rate=win_rate,
        opening_hand_win_rate=win_rate,
        drawn_improvement_win_rate=0.0,
        sample_counts=RatingSampleCounts(
            seen=2_000,
            picked=1_500,
            games_played=1_200,
            opening_hand=800,
            games_in_hand=1_000,
        ),
    )


def _two_account_log_lines() -> list[str]:
    return [
        _auth_line(client_id="ACCOUNT-A", screen_name="First"),
        _course_line(
            event_name="QuickDraft_ABC_20260703",
            course_id="draft-a",
        ),
        _pack_line(
            event_name="QuickDraft_ABC_20260703",
            pack_number=0,
            pick_number=0,
            draft_pack=(101, 102),
            picked_cards=(),
        ),
        _pick_request_line(
            event_name="QuickDraft_ABC_20260703",
            request_id="pick-a",
            card_id=101,
            pack_number=0,
            pick_number=0,
        ),
        _completed_line(
            event_name="QuickDraft_ABC_20260703",
            pack_number=0,
            pick_number=0,
            picked_cards=(101,),
        ),
        _auth_line(client_id="ACCOUNT-B", screen_name="Second"),
        _course_line(
            event_name="QuickDraft_DEF_20260703",
            course_id="draft-b",
        ),
        _pack_line(
            event_name="QuickDraft_DEF_20260703",
            pack_number=0,
            pick_number=0,
            draft_pack=(201, 202),
            picked_cards=(),
        ),
        _pick_request_line(
            event_name="QuickDraft_DEF_20260703",
            request_id="pick-b",
            card_id=201,
            pack_number=0,
            pick_number=0,
        ),
        _completed_line(
            event_name="QuickDraft_DEF_20260703",
            pack_number=0,
            pick_number=0,
            picked_cards=(201,),
        ),
    ]


def _auth_line(*, client_id: str, screen_name: str) -> str:
    return json.dumps(
        {
            "authenticateResponse": {
                "clientId": client_id,
                "screenName": screen_name,
            },
        }
    )


def _course_line(*, event_name: str, course_id: str) -> str:
    return json.dumps(
        {
            "Course": {
                "CourseId": course_id,
                "InternalEventName": event_name,
                "CurrentModule": "BotDraft",
            },
        }
    )


def _pack_line(
    *,
    event_name: str,
    pack_number: int,
    pick_number: int,
    draft_pack: tuple[int, ...],
    picked_cards: tuple[int, ...],
) -> str:
    return _payload_line(
        module="BotDraft",
        payload={
            "Result": "Success",
            "EventName": event_name,
            "DraftStatus": "PickNext",
            "PackNumber": pack_number,
            "PickNumber": pick_number,
            "NumCardsToPick": 1,
            "DraftPack": [str(grp_id) for grp_id in draft_pack],
            "PickedCards": [str(grp_id) for grp_id in picked_cards],
        },
    )


def _pick_request_line(
    *,
    event_name: str,
    request_id: str,
    card_id: int,
    pack_number: int,
    pick_number: int,
) -> str:
    request = {
        "EventName": event_name,
        "PickInfo": {
            "EventName": event_name,
            "CardIds": [str(card_id)],
            "PackNumber": pack_number,
            "PickNumber": pick_number,
        },
    }
    envelope = {"id": request_id, "request": json.dumps(request)}
    return f"[UnityCrossThreadLogger]==> BotDraftDraftPick {json.dumps(envelope)}"


def _completed_line(
    *,
    event_name: str,
    pack_number: int,
    pick_number: int,
    picked_cards: tuple[int, ...],
) -> str:
    return _payload_line(
        module="DeckSelect",
        payload={
            "Result": "Success",
            "EventName": event_name,
            "DraftStatus": "Completed",
            "PackNumber": pack_number,
            "PickNumber": pick_number,
            "NumCardsToPick": 1,
            "DraftPack": [],
            "PickedCards": [str(grp_id) for grp_id in picked_cards],
        },
    )


def _payload_line(*, module: str, payload: dict[str, object]) -> str:
    return json.dumps({"CurrentModule": module, "Payload": json.dumps(payload)})


def _append_lines(*, path: Path, lines: list[str]) -> None:
    with path.open("a", encoding="utf-8") as log_file:
        for line in lines:
            log_file.write(f"{line}\n")


def _write_lines(*, path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _line_count(*, output: str, prefix: str) -> int:
    return sum(1 for line in output.splitlines() if line.startswith(prefix))
