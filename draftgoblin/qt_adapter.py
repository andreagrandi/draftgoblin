"""Translate shared live-session state and commands for Qt frontends.
Keep blocking session work on one worker thread and QML values presentation-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, cast

from PySide6.QtCore import (
    QByteArray,
    QAbstractListModel,
    QModelIndex,
    QMetaObject,
    QObject,
    Property,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)

from draftgoblin.ranking import RankingMode
from draftgoblin.session import (
    ChangeRanking,
    ChangeSplashPreference,
    ChooseRecommendation,
    DismissError,
    LiveSession,
    LiveSessionCommand,
    LiveSessionSnapshot,
    RequestBacktest,
    RequestBuild,
    RequestRatingsDownload,
    RetryError,
    SnapshotPublisher,
)

SessionFactory = Callable[[SnapshotPublisher], LiveSession]
_OMITTED_SNAPSHOT_FIELDS = frozenset(("current_pack_event", "current_scored_pack"))


_WORKER_ERROR_ID = "qt-worker-error"


def _to_qml_value(value: Any) -> Any:
    """Convert immutable application values into plain QML-safe values.
    Domain-only and event payloads never cross the frontend boundary.
    """

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_qml_value(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("domain_")
            and field.name not in _OMITTED_SNAPSHOT_FIELDS
        }
    if isinstance(value, tuple):
        return [_to_qml_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _to_qml_value(item)
            for key, item in value.items()
        }
    return value


class RecommendationListModel(QAbstractListModel):
    """Publish recommendation rows through a narrow Qt item model.
    Each row is the same plain mapping available in the immutable state projection.
    """

    MODEL_DATA_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, QByteArray]:
        return {self.MODEL_DATA_ROLE: QByteArray(b"modelData")}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        if role == self.MODEL_DATA_ROLE:
            return self._rows[index.row()]
        return None

    def replace(self, *, rows: list[dict[str, Any]]) -> None:
        if rows == self._rows:
            return
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class SessionAdapter(QObject):
    """Expose one QML-facing provider contract for mock and live sessions.
    Subclasses choose synchronous mock dispatch or queued live dispatch.
    """

    stateChanged = Signal()
    scenarioChanged = Signal()

    def __init__(
        self,
        *,
        snapshot: LiveSessionSnapshot | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state: dict[str, Any] = {}
        self._state_before_failure: dict[str, Any] | None = None
        self._recommendations_model = RecommendationListModel(parent=self)
        self._publish(snapshot=LiveSessionSnapshot() if snapshot is None else snapshot)

    @Property("QVariantMap", notify=stateChanged)
    def state(self) -> dict[str, Any]:
        return self._state

    @Property(QObject, constant=True)
    def recommendationsModel(self) -> RecommendationListModel:
        return self._recommendations_model

    @Property(bool, constant=True)
    def mockMode(self) -> bool:
        return False

    @Property(str, notify=scenarioChanged)
    def scenario(self) -> str:
        return ""

    @Property("QStringList", notify=scenarioChanged)
    def scenarios(self) -> list[str]:
        return []

    @Slot(str)
    def selectScenario(self, scenario: str) -> None:
        del scenario

    @Slot(int)
    def chooseRecommendation(self, grp_id: int) -> None:
        self._dispatch(command=ChooseRecommendation(grp_id=grp_id))

    @Slot(str)
    def changeRanking(self, ranking_mode: str) -> None:
        self._dispatch(
            command=ChangeRanking(
                ranking_mode=cast(RankingMode, ranking_mode),
            )
        )

    @Slot(bool)
    def setSplashEnabled(self, enabled: bool) -> None:
        self._dispatch(command=ChangeSplashPreference(enabled=enabled))

    @Slot()
    def requestRatings(self) -> None:
        ratings = self._state.get("ratings", {})
        set_code = ratings.get("set_code") or "OTJ"
        self._dispatch(command=RequestRatingsDownload(set_code=set_code))

    @Slot(str)
    def requestBuild(self, pair_override: str) -> None:
        recommendations = self._state.get("recommendations", {})
        self._dispatch(
            command=RequestBuild(
                pair_override=pair_override or None,
                allow_splash=bool(recommendations.get("splash_enabled", True)),
            )
        )

    @Slot()
    def requestBacktest(self) -> None:
        self._dispatch(command=RequestBacktest())

    @Slot(str)
    def dismissError(self, error_id: str) -> None:
        if error_id == _WORKER_ERROR_ID and self._state_before_failure is not None:
            state = self._state_before_failure
            self._state_before_failure = None
            self._replace_state(state=state)
            return
        self._dispatch(command=DismissError(error_id=error_id))

    @Slot(str)
    def retryError(self, error_id: str) -> None:
        self._dispatch(command=RetryError(error_id=error_id))

    def _dispatch(self, *, command: LiveSessionCommand) -> None:
        raise NotImplementedError

    @Slot(object)
    def _apply_snapshot(self, snapshot: LiveSessionSnapshot) -> None:
        self._state_before_failure = None
        self._publish(snapshot=snapshot)

    @Slot(str)
    def _apply_failure(self, message: str) -> None:
        if self._state_before_failure is None:
            self._state_before_failure = self._state
        state = dict(self._state)
        state["status"] = {
            "phase": "error",
            "message": "Draftgoblin could not complete background work.",
        }
        state["progress"] = None
        state["errors"] = [
            {
                "error_id": _WORKER_ERROR_ID,
                "code": "qt_worker_error",
                "message": message,
                "recoverable": False,
                "operation": None,
            }
        ]
        self._replace_state(state=state)

    def _publish(self, *, snapshot: LiveSessionSnapshot) -> None:
        self._replace_state(state=cast(dict[str, Any], _to_qml_value(snapshot)))

    def _replace_state(self, *, state: dict[str, Any]) -> None:
        if state == self._state:
            return
        self._state = state
        recommendations = state.get("recommendations") or {}
        rows = recommendations.get("cards") or []
        self._recommendations_model.replace(rows=list(rows))
        self.stateChanged.emit()


class _LiveSessionWorker(QObject):
    snapshotReady = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        poll_interval_ms: int,
        startup_scan: bool,
    ) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._poll_interval_ms = poll_interval_ms
        self._startup_scan = startup_scan
        self._session: LiveSession | None = None
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        try:
            self._session = self._session_factory(self.snapshotReady.emit)
            self.snapshotReady.emit(self._session.snapshot)
            if hasattr(self._session, "load_card_data"):
                self._session.load_card_data()
            if self._startup_scan:
                self._session.scan_startup_files(
                    include_previous=True,
                    include_pre_draft_detection=False,
                )
            self._poll()
            self._timer = QTimer(self)
            self._timer.setInterval(self._poll_interval_ms)
            self._timer.timeout.connect(self._poll)
            self._timer.start()
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.failed.emit(str(error))

    @Slot(object)
    def dispatch(self, command: LiveSessionCommand) -> None:
        if self._session is None:
            return
        try:
            self._session.dispatch(command=command)
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.failed.emit(str(error))

    @Slot()
    def _poll(self) -> None:
        if self._session is None:
            return
        try:
            self._session.poll_once()
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.failed.emit(str(error))

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._session is not None:
            try:
                self._session.stop()
            except Exception as error:  # pragma: no cover - defensive UI boundary.
                self.failed.emit(str(error))
        self.finished.emit()


class LiveSessionAdapter(SessionAdapter):
    """Run the production live session behind one queued Qt worker.
    Immutable snapshots return to the GUI thread through queued signals.
    """

    _commandRequested = Signal(object)

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        poll_interval_ms: int,
        startup_scan: bool = True,
        parent: QObject | None = None,
    ) -> None:
        if poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be greater than zero.")
        super().__init__(parent=parent)
        self._session_factory = session_factory
        self._poll_interval_ms = poll_interval_ms
        self._startup_scan = startup_scan
        self.thread: QThread | None = None
        self._worker: _LiveSessionWorker | None = None

    @Slot()
    def start(self) -> None:
        if self.thread is not None and self.thread.isRunning():
            return
        thread = QThread(parent=self)
        worker = _LiveSessionWorker(
            session_factory=self._session_factory,
            poll_interval_ms=self._poll_interval_ms,
            startup_scan=self._startup_scan,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        self._commandRequested.connect(worker.dispatch, Qt.ConnectionType.QueuedConnection)
        worker.snapshotReady.connect(
            self._apply_snapshot,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.failed.connect(self._apply_failure, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        self.thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def shutdown(self) -> None:
        thread = self.thread
        worker = self._worker
        if thread is None or worker is None or not thread.isRunning():
            return
        QMetaObject.invokeMethod(
            worker,
            "stop",
            Qt.ConnectionType.BlockingQueuedConnection,
        )
        thread.quit()
        thread.wait()
        self._worker = None

    def _dispatch(self, *, command: LiveSessionCommand) -> None:
        self._commandRequested.emit(command)

