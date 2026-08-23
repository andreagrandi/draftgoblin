from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from draftgoblin.audit import load_draft_audit_records
from draftgoblin.pool import (
    DraftPoolStore,
    DraftState,
    draft_state_path,
    load_draft_state,
    save_draft_state,
)
from draftgoblin.session import (
    AccountIdentity,
    ApplicationPhase,
    ApplicationStatus,
    BacktestPickResult,
    BacktestResult,
    BuildCard,
    BuildLand,
    BuildPairOption,
    BuildResult,
    CardView,
    ChangeRanking,
    ChangeSplashPreference,
    ChooseAccount,
    ChooseRecommendation,
    DismissError,
    DraftIdentity,
    LiveSession,
    LiveSessionCommand,
    LiveSessionSnapshot,
    OperationKind,
    PoolCard,
    PoolState,
    ProgressState,
    Recommendation,
    RecommendationState,
    RequestBacktest,
    RequestBuild,
    RequestRatingsDownload,
    RetryError,
    SessionError,
)

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURE_LOG_PATH = PROJECT_ROOT / "tests" / "fixtures" / "quick-draft-msh-player.log"
FIXTURE_ACCOUNT_ID = "FIXTURECLIENTID1234567890"
FIXTURE_DRAFT_ID = "00000000-0000-4000-8000-000000000004"


def test_default_live_session_snapshot_has_neutral_initial_state() -> None:
    snapshot = LiveSessionSnapshot()

    assert snapshot.status == ApplicationStatus(
        phase=ApplicationPhase.STARTING,
        message="Starting Draftgoblin.",
    )
    assert snapshot.accounts == ()
    assert snapshot.active_account is None
    assert snapshot.draft is None
    assert snapshot.recommendations == RecommendationState()
    assert snapshot.pool == PoolState()
    assert snapshot.progress is None
    assert snapshot.errors == ()
    assert snapshot.build is None
    assert snapshot.backtest is None


def test_live_session_snapshot_covers_complete_frontend_state_immutably() -> None:
    card = _card()
    account = AccountIdentity(account_id="account-1", screen_name="Player#12345")
    draft = DraftIdentity(
        account_id=account.account_id,
        draft_id="draft-1",
        event_name="QuickDraft_TST_20260823",
        set_code="TST",
        course_id="course-1",
        pack_number=0,
        pick_number=1,
        completed=False,
    )
    recommendation = Recommendation(
        rank=1,
        card=card,
        score=87,
        win_rate=0.61,
        average_last_seen_at=2.4,
        source_label="Quick Draft",
        color_fit="on color",
        no_data=False,
    )
    pool_card = PoolCard(card=card, quantity=2)
    build_card = BuildCard(card=card, quantity=2)
    build = BuildResult(
        selected_pair="WU",
        pair_options=(
            BuildPairOption(
                pair="WU",
                score=81.5,
                selected=True,
                automatic=True,
            ),
        ),
        spells=(build_card,),
        lands=(
            BuildLand(
                name="Plains",
                quantity=9,
                source_colors=("W",),
            ),
        ),
        bench=(),
        deck_size=40,
        warnings=("Fixture warning",),
    )
    backtest = BacktestResult(
        ranking_mode="score",
        rows=(
            BacktestPickResult(
                pack_number=0,
                pick_number=0,
                recommended=card,
                actual=card,
                match=True,
                skipped_reason=None,
                data_source="Quick Draft",
            ),
        ),
        match_count=1,
        compared_count=1,
        skipped_count=0,
        data_sources=("Quick Draft",),
    )
    snapshot = LiveSessionSnapshot(
        status=ApplicationStatus(
            phase=ApplicationPhase.DRAFTING,
            message="Pack 1, pick 2",
        ),
        accounts=(account,),
        active_account=account,
        draft=draft,
        recommendations=RecommendationState(
            ranking_mode="score",
            cards=(recommendation,),
            selected_grp_id=card.grp_id,
            source_summary="Quick Draft",
        ),
        pool=PoolState(
            cards=(pool_card,),
            total_cards=2,
            inferred_pair="WU",
            commitment=0.5,
        ),
        progress=ProgressState(
            operation=OperationKind.RATINGS,
            message="Downloading ratings",
            completed=1,
            total=2,
        ),
        errors=(
            SessionError(
                error_id="ratings-1",
                code="ratings_unavailable",
                message="Ratings are temporarily unavailable.",
                recoverable=True,
                operation=OperationKind.RATINGS,
            ),
        ),
        build=build,
        backtest=backtest,
    )

    assert snapshot.active_account == account
    assert snapshot.draft == draft
    assert snapshot.recommendations.cards == (recommendation,)
    assert snapshot.pool.cards == (pool_card,)
    assert snapshot.progress is not None
    assert snapshot.progress.completed == 1
    assert snapshot.errors[0].recoverable is True
    assert snapshot.build == build
    assert snapshot.backtest == backtest
    with pytest.raises(FrozenInstanceError):
        snapshot.status = ApplicationStatus()
    with pytest.raises(FrozenInstanceError):
        snapshot.pool.total_cards = 3


def test_live_session_commands_capture_explicit_user_intentions() -> None:
    commands: tuple[LiveSessionCommand, ...] = (
        ChooseAccount(account_id="account-1"),
        ChooseRecommendation(grp_id=123),
        ChangeRanking(ranking_mode="win_rate"),
        ChangeSplashPreference(enabled=False),
        RequestRatingsDownload(set_code="TST"),
        RequestBuild(pair_override="WU", allow_splash=False),
        RequestBacktest(account_id="account-1", draft_id="draft-1"),
        DismissError(error_id="ratings-1"),
        RetryError(error_id="ratings-1"),
    )

    assert commands == (
        ChooseAccount(account_id="account-1"),
        ChooseRecommendation(grp_id=123),
        ChangeRanking(ranking_mode="win_rate"),
        ChangeSplashPreference(enabled=False),
        RequestRatingsDownload(set_code="TST"),
        RequestBuild(pair_override="WU", allow_splash=False),
        RequestBacktest(account_id="account-1", draft_id="draft-1"),
        DismissError(error_id="ratings-1"),
        RetryError(error_id="ratings-1"),
    )
    with pytest.raises(FrozenInstanceError):
        commands[0].account_id = "account-2"


def test_session_contract_does_not_import_frontend_frameworks() -> None:
    source = (PROJECT_ROOT / "draftgoblin" / "session.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported_roots = {
        name.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for name in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert imported_roots.isdisjoint(
        {"PyQt6", "PySide6", "qml", "rich", "textual"}
    )


def test_live_session_polling_and_rotation_publish_complete_persisted_lifecycle(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    previous_log_path = tmp_path / "Player-prev.log"
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    published: list[LiveSessionSnapshot] = []
    log_path.write_text("", encoding="utf-8")
    session = LiveSession(
        log_path=log_path,
        app_dir=app_dir,
        poll_interval=0.01,
        snapshot_publisher=published.append,
    )

    _write_lines(path=log_path, lines=fixture_lines[:20])
    first_snapshot = session.poll_once()
    _append_lines(path=log_path, lines=fixture_lines[20:70])
    log_path.rename(previous_log_path)
    _write_lines(path=log_path, lines=fixture_lines[70:])
    completed_snapshot = session.poll_once()
    unchanged_snapshot = session.poll_once()

    assert first_snapshot.status.phase == ApplicationPhase.DRAFTING
    assert first_snapshot.active_account == AccountIdentity(
        account_id=FIXTURE_ACCOUNT_ID,
        screen_name="FixturePlayer",
    )
    assert completed_snapshot.status == ApplicationStatus(
        phase=ApplicationPhase.DRAFT_COMPLETE,
        message="Draft complete.",
    )
    assert completed_snapshot.draft is not None
    assert completed_snapshot.draft.draft_id == FIXTURE_DRAFT_ID
    assert completed_snapshot.draft.pack_number == 2
    assert completed_snapshot.draft.pick_number == 13
    assert completed_snapshot.draft.completed is True
    assert completed_snapshot.pool.total_cards == 42
    assert unchanged_snapshot is completed_snapshot
    assert published[-1] is completed_snapshot
    assert all(isinstance(snapshot, LiveSessionSnapshot) for snapshot in published)

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
    assert audit_records[0]["record_type"] == "draft_started"
    assert sum(record["record_type"] == "choice_made" for record in audit_records) == 42
    assert audit_records[-1]["record_type"] == "draft_completed"


def test_live_session_startup_scan_processes_previous_then_current_once(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    previous_log_path = tmp_path / "Player-prev.log"
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    _write_lines(path=previous_log_path, lines=fixture_lines[:70])
    _write_lines(path=log_path, lines=fixture_lines[70:])
    session = LiveSession(
        log_path=log_path,
        app_dir=app_dir,
        previous_log_path=previous_log_path,
    )

    startup_snapshot = session.scan_startup_files()
    polled_snapshot = session.poll_once()

    assert startup_snapshot.status.phase == ApplicationPhase.DRAFT_COMPLETE
    assert startup_snapshot.pool.total_cards == 42
    assert polled_snapshot is startup_snapshot
    audit_records = load_draft_audit_records(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    )
    assert len(audit_records) == 44


def test_live_session_login_change_clears_prior_account_context(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
    )
    session.process_lines(
        lines=(
            _auth_line(account_id="first-account", screen_name="First"),
            _course_line(event_name="QuickDraft_ONE_20260823", course_id="first-draft"),
        )
    )

    snapshot_after_missing_account = session.process_lines(
        lines=(
            "[Accounts - Login] Logged in successfully. Display Name: Second#12345",
            _course_line(
                event_name="QuickDraft_TWO_20260823",
                course_id="second-draft",
            ),
        )
    )

    assert snapshot_after_missing_account.status == ApplicationStatus(
        phase=ApplicationPhase.WAITING_FOR_DRAFT,
        message="Draft detected; waiting for an Arena account ID.",
    )
    assert snapshot_after_missing_account.active_account is None
    assert snapshot_after_missing_account.draft is None
    assert snapshot_after_missing_account.recommendations == RecommendationState()
    assert snapshot_after_missing_account.pool == PoolState()
    assert snapshot_after_missing_account.progress is None
    assert snapshot_after_missing_account.build is None
    assert snapshot_after_missing_account.backtest is None
    assert not draft_state_path(
        account_id="first-account",
        draft_id="second-draft",
        app_dir=app_dir,
    ).exists()

    second_snapshot = session.process_lines(
        lines=(
            _auth_line(account_id="second-account", screen_name="Second"),
            _course_line(
                event_name="QuickDraft_TWO_20260823",
                course_id="second-draft",
            ),
        )
    )

    assert second_snapshot.active_account == AccountIdentity(
        account_id="second-account",
        screen_name="Second",
    )
    assert second_snapshot.draft is not None
    assert second_snapshot.draft.draft_id == "second-draft"
    assert draft_state_path(
        account_id="second-account",
        draft_id="second-draft",
        app_dir=app_dir,
    ).exists()


def test_live_session_recovers_login_profile_and_selects_latest_account_draft(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    first = _draft_state(
        account_id="alpha-account",
        screen_name="Alpha",
        draft_id="alpha-old",
        updated_at="2026-08-21T10:00:00+00:00",
        pool_grp_ids=(101,),
    )
    latest = replace(
        first,
        draft_id="alpha-latest",
        course_id="alpha-latest",
        updated_at="2026-08-22T10:00:00+00:00",
        pool_grp_ids=(101, 102),
    )
    beta = _draft_state(
        account_id="beta-account",
        screen_name="Beta",
        draft_id="beta-draft",
        updated_at="2026-08-22T09:00:00+00:00",
        pool_grp_ids=(201,),
    )
    for state in (first, latest, beta):
        save_draft_state(state=state, app_dir=app_dir)
    profile_store = DraftPoolStore(app_dir=app_dir)
    profile_store.set_active_account(
        account_id="alpha-account",
        screen_name="Alpha",
    )
    profile_store.set_active_account(
        account_id="beta-account",
        screen_name="Beta",
    )
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
    )

    recovered = session.process_lines(
        lines=(
            "[Accounts - Login] Logged in successfully. Display Name: Alpha#98765",
        )
    )
    selected = session.dispatch(
        command=ChooseAccount(account_id="beta-account"),
    )
    session.process_lines(
        lines=(
            "[Accounts - Login] Logged in successfully. Display Name: Unknown#12345",
        )
    )
    session.dispatch(command=ChooseAccount(account_id="beta-account"))
    continued = session.process_lines(
        lines=(
            _course_line(
                event_name="QuickDraft_TST_20260824",
                course_id="beta-new-draft",
            ),
        )
    )

    assert tuple(account.account_id for account in recovered.accounts) == (
        "alpha-account",
        "beta-account",
    )
    assert recovered.active_account == AccountIdentity(
        account_id="alpha-account",
        screen_name="Alpha",
    )
    assert recovered.draft is not None
    assert recovered.draft.draft_id == "alpha-latest"
    assert recovered.pool.total_cards == 2
    assert selected.active_account == AccountIdentity(
        account_id="beta-account",
        screen_name="Beta",
    )
    assert selected.draft is not None
    assert selected.draft.draft_id == "beta-draft"
    assert selected.pool.total_cards == 1
    assert continued.active_account == selected.active_account
    assert continued.draft is not None
    assert continued.draft.draft_id == "beta-new-draft"
    assert draft_state_path(
        account_id="beta-account",
        draft_id="beta-new-draft",
        app_dir=app_dir,
    ).exists()

    state_payload = json.loads(
        draft_state_path(
            account_id="alpha-account",
            draft_id="alpha-latest",
            app_dir=app_dir,
        ).read_text(encoding="utf-8")
    )
    assert state_payload["schema_version"] == 1
    assert state_payload["account_id"] == "alpha-account"
    assert state_payload["draft_id"] == "alpha-latest"
    account_payload = json.loads(
        (app_dir / "accounts" / "alpha-account.json").read_text(
            encoding="utf-8"
        )
    )
    assert account_payload == {
        "account_id": "alpha-account",
        "schema_version": 1,
        "screen_name": "Alpha",
    }


def test_live_session_rejects_unknown_account_selection(tmp_path: Path) -> None:
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
    )

    with pytest.raises(ValueError, match="Unknown Arena account 'missing-account'"):
        session.dispatch(command=ChooseAccount(account_id="missing-account"))


def _card() -> CardView:
    return CardView(
        grp_id=123,
        name="Fixture Card",
        colors=("W",),
        rarity="uncommon",
        types=("Creature",),
        mana_cost="{1}{W}",
        mana_value=2.0,
        image_path="cache/card.jpg",
    )


def _write_lines(*, path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _append_lines(*, path: Path, lines: list[str]) -> None:
    with path.open("a", encoding="utf-8") as log_file:
        log_file.writelines(f"{line}\n" for line in lines)


def _auth_line(*, account_id: str, screen_name: str) -> str:
    return json.dumps(
        {
            "authenticateResponse": {
                "clientId": account_id,
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


def _draft_state(
    *,
    account_id: str,
    screen_name: str,
    draft_id: str,
    updated_at: str,
    pool_grp_ids: tuple[int, ...],
) -> DraftState:
    return DraftState(
        account_id=account_id,
        account_screen_name=screen_name,
        draft_id=draft_id,
        event_name="QuickDraft_TST_20260823",
        set_code="TST",
        course_id=draft_id,
        started_at="2026-08-20T10:00:00+00:00",
        updated_at=updated_at,
        completed_at=None,
        completed=False,
        picks=(),
        pool_grp_ids=pool_grp_ids,
    )
