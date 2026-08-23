from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QObject, QUrl, Slot

from draftgoblin.mock_session import MockLiveSession
from draftgoblin.qt_adapter import LiveSessionAdapter, SessionAdapter
from draftgoblin.session import (
    ChangeRanking,
    ChangeSplashPreference,
    ChooseAccount,
    ChooseRecommendation,
    CardImageRequest,
    CardImageState,
    CardView,
    DataLoadPhase,
    DismissError,
    LiveSession,
    LiveSessionCommand,
    LiveSessionSnapshot,
    Recommendation,
    RecommendationState,
    RequestBacktest,
    RequestBuild,
    RequestRatingsDownload,
    RetryError,
    SnapshotPublisher,
)


class _FakeSession:
    def __init__(self, *, publish: SnapshotPublisher) -> None:
        self._model = MockLiveSession()
        self._publish = publish
        self.snapshot = self._model.snapshot
        self.factory_thread_id: int | None = None
        self.startup_thread_ids: list[int] = []
        self.poll_thread_ids: list[int] = []
        self.startup_options: list[tuple[bool, bool]] = []
        self.dispatch_thread_ids: list[int] = []
        self.commands: list[LiveSessionCommand] = []

    def scan_startup_files(
        self,
        *,
        include_previous: bool = True,
        include_pre_draft_detection: bool = True,
    ) -> LiveSessionSnapshot:
        self.startup_thread_ids.append(threading.get_ident())
        self.startup_options.append(
            (include_previous, include_pre_draft_detection)
        )
        self._publish(self.snapshot)
        return self.snapshot

    def poll_once(self) -> LiveSessionSnapshot:
        self.poll_thread_ids.append(threading.get_ident())
        self._publish(self.snapshot)
        return self.snapshot

    def dispatch(self, *, command: LiveSessionCommand) -> LiveSessionSnapshot:
        self.dispatch_thread_ids.append(threading.get_ident())
        self.commands.append(command)
        self.snapshot = self._model.dispatch(command=command)
        self._publish(self.snapshot)
        return self.snapshot

    def stop(self) -> LiveSessionSnapshot:
        return self.snapshot


class _ImageFakeSession(_FakeSession):
    def __init__(self, *, publish: SnapshotPublisher) -> None:
        super().__init__(publish=publish)
        self.request = CardImageRequest(
            generation=1,
            grp_id=1,
            image_uri="https://images.example/fixture.jpg",
        )
        self.fetch_thread_ids: list[int] = []
        self.result_thread_ids: list[int] = []
        self.completions: list[Path] = []
        self._request_pending = True
        self.stopped = False

    def selected_card_image_request(self) -> CardImageRequest | None:
        return self.request if self._request_pending and not self.stopped else None

    def fetch_card_image(self, *, request: CardImageRequest) -> Path:
        assert request == self.request
        self.fetch_thread_ids.append(threading.get_ident())
        return Path("/tmp/fixture card.jpg")

    def complete_card_image_request(
        self,
        *,
        request: CardImageRequest,
        image_path: Path,
    ) -> None:
        assert request == self.request
        self.result_thread_ids.append(threading.get_ident())
        self.completions.append(image_path)
        self._request_pending = False
        self.snapshot = replace(
            self.snapshot,
            card_image=CardImageState(
                grp_id=request.grp_id,
                phase=DataLoadPhase.READY,
                message="Card image ready.",
            ),
        )
        self._publish(self.snapshot)

    def fail_card_image_request(
        self,
        *,
        request: CardImageRequest,
        error_message: str,
    ) -> None:
        raise AssertionError(f"Unexpected image failure: {request} {error_message}")

    def stop(self) -> LiveSessionSnapshot:
        self.stopped = True
        return self.snapshot


class _StateObserver(QObject):
    def __init__(self, *, adapter: LiveSessionAdapter) -> None:
        super().__init__()
        self._adapter = adapter
        self.thread_ids: list[int] = []
        self.states: list[dict[str, object]] = []

    @Slot()
    def observe(self) -> None:
        self.thread_ids.append(threading.get_ident())
        self.states.append(self._adapter.state)


@pytest.fixture
def qcore_application() -> QCoreApplication:
    return cast(QCoreApplication, QCoreApplication.instance() or QCoreApplication([]))


def test_session_adapter_converts_local_image_path_to_file_url(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "card images" / "Fixture Card.jpg"
    snapshot = LiveSessionSnapshot(
        recommendations=RecommendationState(
            cards=(
                Recommendation(
                    rank=1,
                    card=CardView(
                        grp_id=1,
                        name="Fixture Card",
                        colors=(),
                        rarity="common",
                        types=("Creature",),
                        mana_cost=None,
                        mana_value=1.0,
                        image_path=str(image_path),
                    ),
                    score=1,
                    win_rate=None,
                    average_last_seen_at=None,
                    source_label="Fixture",
                    color_fit="on_color",
                    no_data=False,
                ),
            ),
            selected_grp_id=1,
        )
    )

    adapter = SessionAdapter(snapshot=snapshot)

    assert adapter.state["recommendations"]["cards"][0]["card"]["image_path"] == (
        QUrl.fromLocalFile(str(image_path)).toString()
    )

def _process_until(
    *,
    application: QCoreApplication,
    predicate: Callable[[], bool],
    description: str,
) -> None:
    deadline = time.monotonic() + 3.0
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            pytest.fail(f"Timed out waiting for {description}.")
        time.sleep(0.001)
    application.processEvents()


def test_live_adapter_runs_session_work_on_worker_and_queues_plain_snapshots(
    qcore_application: QCoreApplication,
) -> None:
    gui_thread_id = threading.get_ident()
    sessions: list[_FakeSession] = []

    def factory(publish: SnapshotPublisher) -> LiveSession:
        session = _FakeSession(publish=publish)
        session.factory_thread_id = threading.get_ident()
        sessions.append(session)
        return cast(LiveSession, session)

    adapter = LiveSessionAdapter(
        session_factory=factory,
        poll_interval_ms=60_000,
    )
    assert adapter.thread is None
    observer = _StateObserver(adapter=adapter)
    adapter.stateChanged.connect(observer.observe)

    try:
        assert adapter.mockMode is False
        assert isinstance(adapter.scenario, str)
        assert isinstance(adapter.scenarios, list)

        adapter.start()
        _process_until(
            application=qcore_application,
            predicate=lambda: bool(sessions)
            and bool(sessions[0].startup_thread_ids)
            and bool(sessions[0].poll_thread_ids)
            and bool(adapter.state),
            description="the live session startup snapshot",
        )
        assert adapter.thread is not None
        assert adapter.thread.isRunning()

        session = sessions[0]
        assert session.factory_thread_id != gui_thread_id
        assert all(thread_id != gui_thread_id for thread_id in session.startup_thread_ids)
        assert all(thread_id != gui_thread_id for thread_id in session.poll_thread_ids)
        assert session.startup_options == [(True, False)]
        assert observer.thread_ids
        assert set(observer.thread_ids) == {gui_thread_id}
        assert observer.states[-1] == adapter.state
        assert "domain_pool" not in adapter.state["build"]
        assert "domain_selection" not in adapter.state["build"]
    finally:
        adapter.shutdown()

    assert adapter.thread is not None
    assert not adapter.thread.isRunning()


def test_live_adapter_queues_explicit_commands_and_shutdown_is_safe(
    qcore_application: QCoreApplication,
) -> None:
    gui_thread_id = threading.get_ident()
    sessions: list[_FakeSession] = []

    def factory(publish: SnapshotPublisher) -> LiveSession:
        session = _FakeSession(publish=publish)
        session.factory_thread_id = threading.get_ident()
        sessions.append(session)
        return cast(LiveSession, session)

    adapter = LiveSessionAdapter(
        session_factory=factory,
        poll_interval_ms=60_000,
    )

    assert adapter.thread is None
    adapter.shutdown()
    assert adapter.thread is None
    adapter.start()
    try:
        _process_until(
            application=qcore_application,
            predicate=lambda: bool(sessions) and bool(adapter.state),
            description="the live session initial state",
        )
        session = sessions[0]

        account_id = adapter.state["accounts"][0]["account_id"]

        adapter.chooseAccount(account_id)
        selected_grp_id = adapter.state["recommendations"]["cards"][1]["card"]["grp_id"]

        adapter.chooseRecommendation(selected_grp_id)
        _process_until(
            application=qcore_application,
            predicate=lambda: adapter.state["recommendations"].get("selected_grp_id")
            == selected_grp_id,
            description="the selected recommendation snapshot",
        )

        adapter.changeRanking("win_rate")
        adapter.setSplashEnabled(False)
        adapter.requestRatings()
        adapter.requestBuild("BG")
        adapter.requestBacktest()
        adapter.dismissError("missing-error")
        adapter.retryError("missing-error")
        _process_until(
            application=qcore_application,
            predicate=lambda: len(session.commands) == 9,
            description="all queued live session commands",
        )

        assert [type(command) for command in session.commands] == [
            ChooseAccount,
            ChooseRecommendation,
            ChangeRanking,
            ChangeSplashPreference,
            RequestRatingsDownload,
            RequestBuild,
            RequestBacktest,
            DismissError,
            RetryError,
        ]
        assert session.dispatch_thread_ids
        assert all(thread_id != gui_thread_id for thread_id in session.dispatch_thread_ids)
    finally:
        adapter.shutdown()

    assert adapter.thread is not None
    assert not adapter.thread.isRunning()


def test_live_adapter_fetches_card_images_on_worker_and_publishes_on_gui_thread(
    qcore_application: QCoreApplication,
) -> None:
    gui_thread_id = threading.get_ident()
    sessions: list[_ImageFakeSession] = []

    def factory(publish: SnapshotPublisher) -> LiveSession:
        session = _ImageFakeSession(publish=publish)
        sessions.append(session)
        return cast(LiveSession, session)

    adapter = LiveSessionAdapter(
        session_factory=factory,
        poll_interval_ms=60_000,
    )
    observer = _StateObserver(adapter=adapter)
    adapter.stateChanged.connect(observer.observe)
    adapter.start()
    try:
        _process_until(
            application=qcore_application,
            predicate=lambda: bool(sessions)
            and bool(sessions[0].completions)
            and adapter.state["card_image"]["phase"] == "ready",
            description="the worker-fetched card image snapshot",
        )
        session = sessions[0]
        assert session.fetch_thread_ids
        assert session.result_thread_ids
        assert set(session.fetch_thread_ids) == set(session.result_thread_ids)
        assert all(thread_id != gui_thread_id for thread_id in session.fetch_thread_ids)
        assert observer.thread_ids
        assert set(observer.thread_ids) == {gui_thread_id}
        assert observer.states[-1]["card_image"]["phase"] == "ready"
    finally:
        adapter.shutdown()

    assert sessions[0].stopped is True
    assert adapter.thread is not None
    assert not adapter.thread.isRunning()


def test_live_adapter_surfaces_and_dismisses_worker_initialization_errors(
    qcore_application: QCoreApplication,
) -> None:
    def factory(publish: SnapshotPublisher) -> LiveSession:
        del publish
        raise RuntimeError("Live provider failed.")

    adapter = LiveSessionAdapter(
        session_factory=factory,
        poll_interval_ms=60_000,
    )
    adapter.start()
    try:
        _process_until(
            application=qcore_application,
            predicate=lambda: bool(adapter.state["errors"]),
            description="the live provider error",
        )

        assert adapter.state["errors"][0]["message"] == "Live provider failed."
        assert adapter.state["errors"][0]["recoverable"] is False

        adapter.dismissError("qt-worker-error")

        assert adapter.state["errors"] == []
    finally:
        adapter.shutdown()
