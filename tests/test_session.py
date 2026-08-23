from __future__ import annotations

import ast
import json
import threading
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import draftgoblin.session as session_module
from draftgoblin.audit import load_draft_audit_records
from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.events import (
    AccountEvent,
    DraftStartedEvent,
    PackOfferedEvent,
    QuickDraftDetectedEvent,
)
from draftgoblin.pool import (
    DraftPick,
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
    CardDataState,
    CardView,
    ChangeRanking,
    ChangeSplashPreference,
    ChooseAccount,
    ChooseRecommendation,
    DataLoadPhase,
    DismissError,
    DraftIdentity,
    LiveSession,
    LiveSessionCommand,
    LiveSessionEvent,
    LiveSessionSnapshot,
    OperationKind,
    PoolCard,
    PoolState,
    ProgressState,
    RatingsLoader,
    RatingsState,
    Recommendation,
    RecommendationState,
    RequestBacktest,
    RequestBuild,
    RequestRatingsDownload,
    RetryError,
    SessionError,
)
from draftgoblin.seventeen import (
    DownloadProgressCallback,
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    RatingSampleCounts,
    SeventeenCardStats,
    SeventeenLandsData,
    SeventeenLandsDownloadProgress,
    SeventeenLandsFormatData,
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
    assert snapshot.card_data == CardDataState()
    assert snapshot.ratings == RatingsState()
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


def test_live_session_publishes_consumed_events_with_resulting_state(
    tmp_path: Path,
) -> None:
    published: list[LiveSessionEvent] = []
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        event_publisher=published.append,
    )
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()

    session.process_lines(lines=fixture_lines[:7])

    assert [type(item.event) for item in published] == [
        AccountEvent,
        QuickDraftDetectedEvent,
        DraftStartedEvent,
        PackOfferedEvent,
    ]
    assert published[-1].snapshot is session.snapshot
    assert published[-1].scored_pack is not None
    assert published[-1].snapshot.recommendations.cards


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


def test_live_session_loads_cached_ratings_scores_all_ranking_modes_and_audits_choice(
    tmp_path: Path,
) -> None:
    cache_checks: list[str] = []
    rating_loads: list[str] = []

    def cache_checker(set_code: str) -> bool:
        cache_checks.append(set_code)
        return True

    def ratings_loader(set_code: str) -> SeventeenLandsData:
        rating_loads.append(set_code)
        return _fixture_ratings_data(set_code=set_code)

    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        ratings_loader=ratings_loader,
        ratings_cache_checker=cache_checker,
    )
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    pack_line_index = _process_until_recommendations(
        session=session,
        lines=fixture_lines,
    )

    assert cache_checks == ["MSH"]
    assert rating_loads == ["MSH"]
    assert session.snapshot.card_data.phase == DataLoadPhase.READY
    assert session.snapshot.ratings == RatingsState(
        set_code="MSH",
        phase=DataLoadPhase.READY,
        message="17Lands ratings are ready for MSH.",
        rated_cards=2,
        total_cards=14,
    )
    assert session.snapshot.recommendations.source_summary == (
        "QuickDraft + Premier fallback + neutral prior"
    )
    assert {card.source_label for card in session.snapshot.recommendations.cards} == {
        "Premier",
        "Prior*",
        "Quick",
    }

    top_cards_by_mode: dict[str, int] = {}
    supported_modes = session.snapshot.recommendations.supported_ranking_modes
    for ranking_mode in supported_modes:
        snapshot = session.dispatch(
            command=ChangeRanking(ranking_mode=ranking_mode),
        )
        top_cards_by_mode[ranking_mode] = snapshot.recommendations.cards[0].card.grp_id
        assert snapshot.recommendations.ranking_mode == ranking_mode
        assert tuple(card.rank for card in snapshot.recommendations.cards) == tuple(
            range(1, 15)
        )

    assert top_cards_by_mode == {
        "score": 104894,
        "win_rate": 104894,
        "alsa": 104976,
        "mv": 105080,
    }
    expected_recommendation = top_cards_by_mode["mv"]
    for line in fixture_lines[pack_line_index + 1 :]:
        session.process_lines(lines=(line,))
        records = load_draft_audit_records(
            account_id=FIXTURE_ACCOUNT_ID,
            draft_id=FIXTURE_DRAFT_ID,
            app_dir=tmp_path / "app",
        )
        if records and records[-1]["record_type"] == "choice_made":
            break

    assert records[-2]["record_type"] == "decision_evaluated"
    assert records[-1]["record_type"] == "choice_made"
    assert records[-1]["ranking_mode"] == "mv"
    assert records[-1]["recommended_grp_id"] == expected_recommendation
    assert records[-1]["evaluation_id"] == records[-2]["evaluation_id"]


def test_live_session_uses_one_based_later_pick_index_in_scores_and_audit(
    tmp_path: Path,
) -> None:
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
    )
    for line in FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines():
        snapshot = session.process_lines(lines=(line,))
        if (
            snapshot.draft is not None
            and snapshot.draft.pack_number == 0
            and snapshot.draft.pick_number == 5
            and snapshot.recommendations.cards
        ):
            break

    records = load_draft_audit_records(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=tmp_path / "app",
    )
    evaluation = records[-1]

    assert evaluation["record_type"] == "decision_evaluated"
    assert evaluation["pack_number"] == 0
    assert evaluation["pick_number"] == 5
    assert evaluation["pick_index"] == 6
    assert evaluation["commitment"]["pick_index"] == 6
    assert evaluation["commitment"]["level"] > 0.0


def test_live_session_missing_ratings_use_neutral_priors_until_download_completes(
    tmp_path: Path,
) -> None:
    published: list[LiveSessionSnapshot] = []
    load_calls: list[str] = []

    def ratings_loader(
        set_code: str,
        progress_callback: DownloadProgressCallback,
    ) -> SeventeenLandsData:
        load_calls.append(set_code)
        progress_callback(
            SeventeenLandsDownloadProgress(
                completed_requests=1,
                total_requests=4,
                message="Downloaded Quick Draft ratings",
            )
        )
        progress_callback(
            SeventeenLandsDownloadProgress(
                completed_requests=4,
                total_requests=4,
                message="Downloaded Premier Draft ratings",
            )
        )
        return _fixture_ratings_data(set_code=set_code)

    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        ratings_progress_loader=ratings_loader,
        ratings_cache_checker=lambda set_code: False,
        snapshot_publisher=published.append,
    )
    fixture_lines = FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    _process_until_recommendations(session=session, lines=fixture_lines)

    assert load_calls == []
    assert session.snapshot.ratings.phase == DataLoadPhase.MISSING
    assert session.snapshot.recommendations.source_summary == "neutral prior"
    assert all(card.no_data for card in session.snapshot.recommendations.cards)
    assert all(card.score == 50 for card in session.snapshot.recommendations.cards)

    published.clear()
    downloaded = session.dispatch(command=RequestRatingsDownload(set_code="MSH"))

    assert load_calls == ["MSH"]
    assert downloaded.ratings.phase == DataLoadPhase.READY
    assert downloaded.progress is None
    assert downloaded.recommendations.cards[0].card.grp_id == 104894
    assert any(
        snapshot.ratings.phase == DataLoadPhase.LOADING
        and snapshot.progress == ProgressState(
            operation=OperationKind.RATINGS,
            message="Downloaded Quick Draft ratings",
            completed=1,
            total=4,
        )
        for snapshot in published
    )
    assert any(
        snapshot.progress is not None and snapshot.progress.completed == 4
        for snapshot in published
    )


def test_live_session_failed_ratings_download_is_recoverable_and_retry_rescores(
    tmp_path: Path,
) -> None:
    attempts = 0

    def ratings_loader(set_code: str) -> SeventeenLandsData:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary service failure")

        return _fixture_ratings_data(set_code=set_code)

    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        ratings_loader=ratings_loader,
        ratings_cache_checker=lambda set_code: False,
    )
    _process_until_recommendations(
        session=session,
        lines=FIXTURE_LOG_PATH.read_text(encoding="utf-8").splitlines(),
    )

    failed = session.dispatch(command=RequestRatingsDownload(set_code="MSH"))

    assert failed.ratings.phase == DataLoadPhase.FAILED
    assert failed.progress is None
    assert failed.errors == (
        SessionError(
            error_id="ratings:MSH",
            code="ratings_unavailable",
            message="17Lands ratings failed for MSH: temporary service failure.",
            recoverable=True,
            operation=OperationKind.RATINGS,
        ),
    )
    assert failed.recommendations.source_summary == "neutral prior"

    recovered = session.dispatch(command=RetryError(error_id="ratings:MSH"))

    assert attempts == 2
    assert recovered.ratings.phase == DataLoadPhase.READY
    assert recovered.errors == ()
    assert recovered.recommendations.cards[0].card.grp_id == 104894


def test_live_session_card_data_load_failure_and_retry_publish_complete_states(
    tmp_path: Path,
) -> None:
    attempts = 0
    published: list[LiveSessionSnapshot] = []

    def card_database_loader() -> CardDatabase:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("card cache unavailable")

        return _fixture_card_database()

    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database_loader=card_database_loader,
        snapshot_publisher=published.append,
    )

    assert session.snapshot.card_data.phase == DataLoadPhase.IDLE

    failed = session.load_card_data()

    assert failed.card_data.phase == DataLoadPhase.FAILED
    assert failed.progress is None
    assert failed.errors[0].error_id == "card-data"
    assert any(
        snapshot.card_data.phase == DataLoadPhase.LOADING
        and snapshot.progress is not None
        and snapshot.progress.operation == OperationKind.CARD_DATA
        for snapshot in published
    )

    ready = session.dispatch(command=RetryError(error_id="card-data"))

    assert attempts == 2
    assert ready.card_data == CardDataState(
        phase=DataLoadPhase.READY,
        message="Card metadata is ready.",
    )
    assert ready.errors == ()


def test_live_session_resumes_deferred_ratings_after_card_data_becomes_ready(
    tmp_path: Path,
) -> None:
    factory_calls: list[CardDatabase] = []
    cache_checks: list[str] = []
    rating_loads: list[str] = []

    def ratings_loader_factory(database: CardDatabase) -> RatingsLoader:
        factory_calls.append(database)

        def ratings_loader(set_code: str) -> SeventeenLandsData:
            rating_loads.append(set_code)
            return _fixture_ratings_data(set_code=set_code)

        return ratings_loader

    def cache_checker(set_code: str) -> bool:
        cache_checks.append(set_code)
        return True

    database = _fixture_card_database()
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database_loader=lambda: database,
        ratings_loader_factory=ratings_loader_factory,
        ratings_cache_checker=cache_checker,
    )
    waiting = session.process_lines(
        lines=(
            _auth_line(account_id="account-1", screen_name="Player"),
            _course_line(
                event_name="QuickDraft_TST_20260823",
                course_id="draft-tst",
            ),
        )
    )

    assert waiting.card_data.phase == DataLoadPhase.IDLE
    assert waiting.ratings.phase == DataLoadPhase.IDLE
    assert waiting.ratings.set_code == "TST"
    assert factory_calls == []
    assert cache_checks == []
    assert rating_loads == []

    ready = session.load_card_data()

    assert factory_calls == [database]
    assert cache_checks == ["TST"]
    assert rating_loads == ["TST"]
    assert ready.card_data.phase == DataLoadPhase.READY
    assert ready.ratings.phase == DataLoadPhase.READY
    assert ready.ratings.set_code == "TST"


def test_inactive_ratings_worker_caches_result_without_publishing_stale_state(
    tmp_path: Path,
) -> None:
    load_started = threading.Event()
    release_load = threading.Event()
    load_calls: list[str] = []
    worker_errors: list[BaseException] = []
    published: list[LiveSessionSnapshot] = []

    def ratings_loader(
        set_code: str,
        progress_callback: DownloadProgressCallback,
    ) -> SeventeenLandsData:
        load_calls.append(set_code)
        load_started.set()
        assert release_load.wait(timeout=2.0)
        progress_callback(
            SeventeenLandsDownloadProgress(
                completed_requests=4,
                total_requests=4,
                message=f"Downloaded ratings for {set_code}",
            )
        )
        return _fixture_ratings_data(set_code=set_code)

    def request_download() -> None:
        try:
            session.dispatch(command=RequestRatingsDownload(set_code="AAA"))
        except BaseException as error:
            worker_errors.append(error)

    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=tmp_path / "app",
        card_database=_fixture_card_database(),
        ratings_progress_loader=ratings_loader,
        ratings_cache_checker=lambda set_code: False,
        snapshot_publisher=published.append,
    )
    session.process_lines(
        lines=(
            _auth_line(account_id="account-1", screen_name="Player"),
            _course_line(
                event_name="QuickDraft_AAA_20260823",
                course_id="draft-aaa",
            ),
        )
    )
    worker = threading.Thread(target=request_download, daemon=True)
    worker.start()
    assert load_started.wait(timeout=2.0)

    try:
        session.dispatch(command=RequestRatingsDownload(set_code="AAA"))
        switched = session.process_lines(
            lines=(
                _course_line(
                    event_name="QuickDraft_BBB_20260823",
                    course_id="draft-bbb",
                ),
            )
        )
        assert load_calls == ["AAA"]
        assert switched.draft is not None
        assert switched.draft.set_code == "BBB"
        assert switched.ratings.set_code == "BBB"
        assert switched.ratings.phase == DataLoadPhase.MISSING
        published.clear()
    finally:
        release_load.set()
        worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert worker_errors == []
    assert published == []
    assert session.snapshot.draft is not None
    assert session.snapshot.draft.set_code == "BBB"
    assert session.snapshot.ratings.set_code == "BBB"
    assert session.snapshot.ratings.phase == DataLoadPhase.MISSING
    assert session.snapshot.progress is None

    restored = session.process_lines(
        lines=(
            _course_line(
                event_name="QuickDraft_AAA_20260823",
                course_id="draft-aaa",
            ),
        )
    )

    assert load_calls == ["AAA"]
    assert restored.draft is not None
    assert restored.draft.set_code == "AAA"
    assert restored.ratings.set_code == "AAA"
    assert restored.ratings.phase == DataLoadPhase.READY


def test_live_session_build_request_publishes_structured_ordered_result(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    database = _fixture_card_database()
    state = _draft_state(
        account_id="account-1",
        screen_name="Player",
        draft_id="draft-1",
        updated_at="2026-08-23T10:00:00+00:00",
        pool_grp_ids=tuple(database.cards),
    )
    save_draft_state(state=state, app_dir=app_dir)
    published: list[LiveSessionSnapshot] = []
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=database,
        snapshot_publisher=published.append,
    )
    session.dispatch(command=ChooseAccount(account_id="account-1"))

    snapshot = session.dispatch(
        command=RequestBuild(pair_override="WU", allow_splash=False)
    )

    assert any(
        item.progress == ProgressState(
            operation=OperationKind.BUILD,
            message="Building deck",
        )
        for item in published
    )
    assert snapshot.progress is None
    assert snapshot.errors == ()
    assert snapshot.build is not None
    assert snapshot.build.selected_pair == "WU"
    assert snapshot.build.pair_override == "WU"
    assert sum(card.quantity for card in snapshot.build.spells) == len(database.cards)
    assert all(card.score is not None for card in snapshot.build.spells)
    assert [
        card.card.mana_value for card in snapshot.build.spells
    ] == sorted(card.card.mana_value for card in snapshot.build.spells)
    assert sum(land.quantity for land in snapshot.build.lands) + sum(
        card.quantity for card in snapshot.build.spells
    ) == snapshot.build.deck_size
    assert any(
        option.pair == "WU" and option.selected and option.automatic
        for option in snapshot.build.pair_options
    )


def test_live_session_build_request_reports_empty_and_invalid_builds(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    database = _fixture_card_database()
    save_draft_state(
        state=_draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-empty",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=(),
        ),
        app_dir=app_dir,
    )
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=database,
    )
    session.dispatch(command=ChooseAccount(account_id="account-1"))

    empty = session.dispatch(command=RequestBuild())

    assert empty.build is None
    assert empty.errors[-1].code == "build_failed"
    assert "pool is empty" in empty.errors[-1].message
    assert empty.errors[-1].recoverable is True

    save_draft_state(
        state=_draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-full",
            updated_at="2026-08-23T11:00:00+00:00",
            pool_grp_ids=tuple(database.cards),
        ),
        app_dir=app_dir,
    )
    session.dispatch(command=ChooseAccount(account_id="account-1"))

    invalid = session.dispatch(command=RequestBuild(pair_override="ZZ"))

    assert invalid.build is None
    assert invalid.errors[-1].code == "build_failed"
    assert "ZZ" in invalid.errors[-1].message


def test_live_session_discards_build_completion_after_account_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir = tmp_path / "app"
    database = _fixture_card_database()
    save_draft_state(
        state=_draft_state(
            account_id="account-a",
            screen_name="Player A",
            draft_id="draft-a",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=tuple(database.cards),
        ),
        app_dir=app_dir,
    )
    save_draft_state(
        state=_draft_state(
            account_id="account-b",
            screen_name="Player B",
            draft_id="draft-b",
            updated_at="2026-08-23T11:00:00+00:00",
            pool_grp_ids=(),
        ),
        app_dir=app_dir,
    )
    started = threading.Event()
    release = threading.Event()
    real_builder = session_module.build_deck_from_pool

    def blocking_builder(*args: object, **kwargs: object) -> object:
        started.set()
        assert release.wait(timeout=2.0)
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(session_module, "build_deck_from_pool", blocking_builder)
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=database,
    )
    session.dispatch(command=ChooseAccount(account_id="account-a"))
    worker_errors: list[Exception] = []

    def request_build() -> None:
        try:
            session.dispatch(command=RequestBuild(pair_override="WU"))
        except Exception as error:
            worker_errors.append(error)

    worker = threading.Thread(target=request_build, daemon=True)
    worker.start()
    assert started.wait(timeout=2.0)

    switched = session.dispatch(command=ChooseAccount(account_id="account-b"))
    assert switched.draft is not None
    assert switched.draft.draft_id == "draft-b"
    assert switched.progress is None

    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert worker_errors == []
    assert session.snapshot.draft is not None
    assert session.snapshot.draft.draft_id == "draft-b"
    assert session.snapshot.build is None
    assert not any(error.operation == OperationKind.BUILD for error in session.snapshot.errors)


def test_live_session_splash_change_discards_in_flight_build_and_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir = tmp_path / "app"
    database = _fixture_card_database()
    save_draft_state(
        state=_draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-1",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=tuple(database.cards),
        ),
        app_dir=app_dir,
    )
    started = threading.Event()
    release = threading.Event()
    real_builder = session_module.build_deck_from_pool

    def blocking_builder(*args: object, **kwargs: object) -> object:
        started.set()
        assert release.wait(timeout=2.0)
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(session_module, "build_deck_from_pool", blocking_builder)
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=database,
    )
    session.dispatch(command=ChooseAccount(account_id="account-1"))
    worker_errors: list[Exception] = []

    def request_build() -> None:
        try:
            session.dispatch(command=RequestBuild(allow_splash=True))
        except Exception as error:
            worker_errors.append(error)

    worker = threading.Thread(
        target=request_build,
        daemon=True,
    )
    worker.start()
    assert started.wait(timeout=2.0)

    changed = session.dispatch(command=ChangeSplashPreference(enabled=False))

    assert changed.progress is None
    assert changed.build is None
    assert changed.recommendations.splash_enabled is False

    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert worker_errors == []
    assert session.snapshot.progress is None
    assert session.snapshot.build is None


def test_live_session_backtest_request_preserves_comparisons_and_missing_history(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    database = _fixture_card_database()
    offered_grp_ids = tuple(database.cards)[:2]
    state = replace(
        _draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-1",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=(offered_grp_ids[0], offered_grp_ids[1]),
        ),
        picks=(
            DraftPick(
                pack_number=0,
                pick_number=0,
                offered_grp_ids=offered_grp_ids,
                pool_before_pick=(),
                chosen_grp_id=offered_grp_ids[0],
            ),
            DraftPick(
                pack_number=0,
                pick_number=1,
                offered_grp_ids=None,
                pool_before_pick=(offered_grp_ids[0],),
                chosen_grp_id=offered_grp_ids[1],
            ),
        ),
    )
    save_draft_state(state=state, app_dir=app_dir)
    published: list[LiveSessionSnapshot] = []
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=database,
        snapshot_publisher=published.append,
    )

    snapshot = session.dispatch(
        command=RequestBacktest(account_id="account-1", draft_id="draft-1")
    )

    assert any(
        item.progress == ProgressState(
            operation=OperationKind.BACKTEST,
            message="Running backtest",
        )
        for item in published
    )
    assert snapshot.progress is None
    assert snapshot.errors == ()
    assert snapshot.backtest is not None
    assert snapshot.backtest.compared_count == 1
    assert snapshot.backtest.skipped_count == 1
    assert snapshot.backtest.match_count == 1
    assert snapshot.backtest.rows[0].recommended is not None
    assert snapshot.backtest.rows[0].actual is not None
    assert snapshot.backtest.rows[0].match is True
    assert snapshot.backtest.rows[0].pool_size == 0
    assert snapshot.backtest.rows[0].offered_count == 2
    assert snapshot.backtest.rows[0].recommended_score is not None
    assert snapshot.backtest.rows[1].skipped_reason == "missing offered-card history"


def test_live_session_backtest_result_identifies_explicit_cross_account_draft(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    active_state = _draft_state(
        account_id="account-a",
        screen_name="Player A",
        draft_id="draft-a",
        updated_at="2026-08-23T10:00:00+00:00",
        pool_grp_ids=(),
    )
    target_state = replace(
        _draft_state(
            account_id="account-b",
            screen_name="Player B",
            draft_id="draft-b",
            updated_at="2026-08-23T11:00:00+00:00",
            pool_grp_ids=(),
        ),
        completed=True,
        completed_at="2026-08-23T11:30:00+00:00",
    )
    save_draft_state(state=active_state, app_dir=app_dir)
    save_draft_state(state=target_state, app_dir=app_dir)
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_fixture_card_database(),
    )
    session.dispatch(command=ChooseAccount(account_id="account-a"))

    snapshot = session.dispatch(
        command=RequestBacktest(account_id="account-b", draft_id="draft-b")
    )

    assert snapshot.draft is not None
    assert snapshot.draft.account_id == "account-a"
    assert snapshot.backtest is not None
    assert snapshot.backtest.account_id == "account-b"
    assert snapshot.backtest.account_screen_name == "Player B"
    assert snapshot.backtest.draft_id == "draft-b"
    assert snapshot.backtest.set_code == "TST"
    assert snapshot.backtest.event_name == "QuickDraft_TST_20260823"
    assert snapshot.backtest.completed is True
    assert snapshot.backtest.chosen_pick_count == 0


def test_live_session_backtest_request_returns_empty_success(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    save_draft_state(
        state=_draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-empty",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=(),
        ),
        app_dir=app_dir,
    )
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_fixture_card_database(),
    )

    snapshot = session.dispatch(
        command=RequestBacktest(
            account_id="account-1",
            draft_id="draft-empty",
        )
    )

    assert snapshot.errors == ()
    assert snapshot.backtest == BacktestResult(
        ranking_mode="score",
        rows=(),
        match_count=0,
        compared_count=0,
        skipped_count=0,
        data_sources=(),
        account_id="account-1",
        account_screen_name="Player",
        draft_id="draft-empty",
        set_code="TST",
        event_name="QuickDraft_TST_20260823",
        completed=False,
        chosen_pick_count=0,
    )


def test_live_session_backtest_failure_can_retry_saved_request(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_fixture_card_database(),
    )
    command = RequestBacktest(account_id="account-1", draft_id="draft-1")

    failed = session.dispatch(command=command)

    assert failed.backtest is None
    assert failed.errors[-1].code == "backtest_failed"
    assert failed.errors[-1].recoverable is True

    save_draft_state(
        state=_draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-1",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=(),
        ),
        app_dir=app_dir,
    )

    retried = session.dispatch(command=RetryError(error_id="backtest"))

    assert retried.errors == ()
    assert retried.backtest is not None
    assert retried.backtest.rows == ()


def test_live_session_discards_older_overlapping_backtest_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir = tmp_path / "app"
    for draft_id, updated_at in (
        ("draft-1", "2026-08-23T10:00:00+00:00"),
        ("draft-2", "2026-08-23T11:00:00+00:00"),
    ):
        save_draft_state(
            state=_draft_state(
                account_id="account-1",
                screen_name="Player",
                draft_id=draft_id,
                updated_at=updated_at,
                pool_grp_ids=(),
            ),
            app_dir=app_dir,
        )
    started = threading.Event()
    release = threading.Event()
    real_generator = session_module.generate_backtest_report

    def blocking_generator(*args: object, **kwargs: object) -> object:
        state = kwargs["state"]
        assert isinstance(state, DraftState)
        if state.draft_id == "draft-1":
            started.set()
            assert release.wait(timeout=2.0)

        return real_generator(*args, **kwargs)

    monkeypatch.setattr(
        session_module,
        "generate_backtest_report",
        blocking_generator,
    )
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_fixture_card_database(),
    )
    worker_errors: list[Exception] = []

    def request_first_backtest() -> None:
        try:
            session.dispatch(
                command=RequestBacktest(
                    account_id="account-1",
                    draft_id="draft-1",
                )
            )
        except Exception as error:
            worker_errors.append(error)

    worker = threading.Thread(target=request_first_backtest, daemon=True)
    worker.start()
    assert started.wait(timeout=2.0)

    newer = session.dispatch(
        command=RequestBacktest(
            account_id="account-1",
            draft_id="draft-2",
        )
    )
    assert newer.backtest is not None
    assert newer.backtest.draft_id == "draft-2"

    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert worker_errors == []
    assert session.snapshot.backtest is not None
    assert session.snapshot.backtest.draft_id == "draft-2"


def test_live_session_ranking_change_discards_in_flight_backtest_and_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir = tmp_path / "app"
    save_draft_state(
        state=_draft_state(
            account_id="account-1",
            screen_name="Player",
            draft_id="draft-1",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=(),
        ),
        app_dir=app_dir,
    )
    started = threading.Event()
    release = threading.Event()
    real_generator = session_module.generate_backtest_report

    def blocking_generator(*args: object, **kwargs: object) -> object:
        started.set()
        assert release.wait(timeout=2.0)
        return real_generator(*args, **kwargs)

    monkeypatch.setattr(
        session_module,
        "generate_backtest_report",
        blocking_generator,
    )
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=_fixture_card_database(),
    )
    worker_errors: list[Exception] = []

    def request_backtest() -> None:
        try:
            session.dispatch(
                command=RequestBacktest(
                    account_id="account-1",
                    draft_id="draft-1",
                )
            )
        except Exception as error:
            worker_errors.append(error)

    worker = threading.Thread(
        target=request_backtest,
        daemon=True,
    )
    worker.start()
    assert started.wait(timeout=2.0)

    changed = session.dispatch(command=ChangeRanking(ranking_mode="win_rate"))

    assert changed.progress is None
    assert changed.backtest is None
    assert changed.recommendations.ranking_mode == "win_rate"

    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert worker_errors == []
    assert session.snapshot.progress is None
    assert session.snapshot.backtest is None


def test_live_session_context_change_clears_derived_errors_and_retries(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    database = _fixture_card_database()
    save_draft_state(
        state=_draft_state(
            account_id="account-a",
            screen_name="Player A",
            draft_id="draft-a",
            updated_at="2026-08-23T10:00:00+00:00",
            pool_grp_ids=(),
        ),
        app_dir=app_dir,
    )
    save_draft_state(
        state=_draft_state(
            account_id="account-b",
            screen_name="Player B",
            draft_id="draft-b",
            updated_at="2026-08-23T11:00:00+00:00",
            pool_grp_ids=tuple(database.cards),
        ),
        app_dir=app_dir,
    )
    session = LiveSession(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        card_database=database,
    )
    session.dispatch(command=ChooseAccount(account_id="account-a"))
    session.dispatch(command=RequestBuild())
    failed = session.dispatch(
        command=RequestBacktest(
            account_id="account-a",
            draft_id="missing-draft",
        )
    )
    assert {
        error.operation for error in failed.errors
    } >= {OperationKind.BUILD, OperationKind.BACKTEST}

    switched = session.dispatch(command=ChooseAccount(account_id="account-b"))

    assert switched.draft is not None
    assert switched.draft.draft_id == "draft-b"
    assert not any(
        error.operation in {OperationKind.BUILD, OperationKind.BACKTEST}
        for error in switched.errors
    )
    with pytest.raises(ValueError, match="Unknown session error 'build'"):
        session.dispatch(command=RetryError(error_id="build"))
    with pytest.raises(ValueError, match="Unknown session error 'backtest'"):
        session.dispatch(command=RetryError(error_id="backtest"))


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


def _process_until_recommendations(
    *,
    session: LiveSession,
    lines: list[str],
) -> int:
    for line_index, line in enumerate(lines):
        snapshot = session.process_lines(lines=(line,))
        if snapshot.recommendations.cards:
            return line_index

    raise AssertionError("Fixture did not publish pack recommendations.")


def _fixture_card_database() -> CardDatabase:
    offered_grp_ids = (
        104894,
        104976,
        105080,
        104995,
        105027,
        105030,
        105170,
        104932,
        104893,
        105091,
        104969,
        105097,
        104979,
        105164,
    )
    mana_values = {
        104894: 4.0,
        104976: 3.0,
        105080: 1.0,
    }
    return CardDatabase(
        cards={
            grp_id: CardInfo(
                grp_id=grp_id,
                name=f"Fixture Card {grp_id}",
                colors=("W", "U"),
                mana_value=mana_values.get(grp_id, 5.0),
                rarity="common",
                types=("Creature",),
            )
            for grp_id in offered_grp_ids
        }
    )


def _fixture_ratings_data(*, set_code: str) -> SeventeenLandsData:
    fetched_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    return SeventeenLandsData(
        set_code=set_code,
        requested_format=QUICK_DRAFT_FORMAT,
        primary=SeventeenLandsFormatData(
            set_code=set_code,
            event_format=QUICK_DRAFT_FORMAT,
            fetched_at=fetched_at,
            card_ratings={
                104894: _fixture_stats(
                    grp_id=104894,
                    gih_win_rate=0.65,
                    average_last_seen_at=3.0,
                ),
            },
            pair_win_rates={},
        ),
        fallback=SeventeenLandsFormatData(
            set_code=set_code,
            event_format=PREMIER_DRAFT_FORMAT,
            fetched_at=fetched_at,
            card_ratings={
                104976: _fixture_stats(
                    grp_id=104976,
                    gih_win_rate=0.60,
                    average_last_seen_at=1.0,
                ),
            },
            pair_win_rates={},
        ),
    )


def _fixture_stats(
    *,
    grp_id: int,
    gih_win_rate: float,
    average_last_seen_at: float,
) -> SeventeenCardStats:
    return SeventeenCardStats(
        grp_id=grp_id,
        name=f"Fixture Card {grp_id}",
        color="",
        rarity="common",
        average_last_seen_at=average_last_seen_at,
        gih_win_rate=gih_win_rate,
        opening_hand_win_rate=gih_win_rate,
        drawn_improvement_win_rate=0.0,
        sample_counts=RatingSampleCounts(
            seen=2_000,
            picked=1_500,
            games_played=1_200,
            opening_hand=800,
            games_in_hand=1_000,
        ),
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
