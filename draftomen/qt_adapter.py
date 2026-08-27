"""Translate shared live-session state and commands for Qt frontends.
Keep blocking session work in adapter-owned workers and QML values presentation-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from os import PathLike
from typing import Any, Literal, TypeAlias, cast

from PySide6.QtCore import (
    QByteArray,
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QMetaObject,
    QObject,
    Property,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QFontInfo, QGuiApplication

from draftomen.preferences import (
    GuiDisplayPreferences,
    load_gui_preferences,
    save_gui_preferences,
)
from draftomen.ranking import RankingMode
from draftomen.session import (
    CardImageFetchResult,
    CardImageRequest,
    ChangeRanking,
    ChangeSplashPreference,
    ChooseAccount,
    ChooseRecommendation,
    DismissError,
    FocusBuildCard,
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
_ImageRequestKind: TypeAlias = Literal["selected", "recommendation", "recent"]
_OMITTED_SNAPSHOT_FIELDS = frozenset(("current_pack_event", "current_scored_pack"))

_DEFAULT_APPLICATION_FONT_PIXEL_SIZE = 13


_WORKER_ERROR_ID = "qt-worker-error"


def _to_qml_value(value: Any) -> Any:
    """Convert immutable application values into plain QML-safe values.
    Domain-only and event payloads never cross the frontend boundary.
    """

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        converted = {
            field.name: _to_qml_value(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("domain_")
            and field.name not in _OMITTED_SNAPSHOT_FIELDS
        }
        image_path = converted.get("image_path")
        if image_path is not None:
            converted["image_path"] = QUrl.fromLocalFile(image_path).toString()
        return converted
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

        previous_count = len(self._rows)
        next_count = len(rows)
        shared_count = min(previous_count, next_count)
        changed_rows = [
            row
            for row in range(shared_count)
            if self._rows[row] != rows[row]
        ]

        if changed_rows:
            self._rows[:shared_count] = rows[:shared_count]
            first_changed = changed_rows[0]
            last_changed = changed_rows[-1]
            self.dataChanged.emit(
                self.index(first_changed, 0),
                self.index(last_changed, 0),
                [self.MODEL_DATA_ROLE],
            )

        if next_count < previous_count:
            self.beginRemoveRows(
                QModelIndex(),
                next_count,
                previous_count - 1,
            )
            del self._rows[next_count:]
            self.endRemoveRows()
            return

        if next_count > previous_count:
            self.beginInsertRows(
                QModelIndex(),
                previous_count,
                next_count - 1,
            )
            self._rows.extend(rows[previous_count:])
            self.endInsertRows()


class GuiPreferencesAdapter(QObject):
    """Expose persisted display-only GUI choices through narrow Qt properties.
    Ranking and splash choices remain explicit commands on SessionAdapter.
    """

    preferencesChanged = Signal()
    persistenceChanged = Signal()
    applicationFontPixelSizeChanged = Signal()

    def __init__(
        self,
        *,
        app_dir: str | PathLike[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_dir = app_dir
        self._preferences, self._persistence_message = load_gui_preferences(
            app_dir=app_dir,
        )
        application = QGuiApplication.instance()
        if isinstance(application, QGuiApplication):
            application.installEventFilter(self)

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ApplicationFontChange:
            self.applicationFontPixelSizeChanged.emit()
        return super().eventFilter(_watched, event)

    @Property(bool, notify=preferencesChanged)
    def compactDensity(self) -> bool:
        return self._preferences.compact_density

    @Property(bool, notify=preferencesChanged)
    def secondaryStats(self) -> bool:
        return self._preferences.secondary_stats

    @Property(bool, notify=preferencesChanged)
    def cardPreview(self) -> bool:
        return self._preferences.card_preview

    @Property(bool, notify=preferencesChanged)
    def detailedBuildContext(self) -> bool:
        return self._preferences.detailed_build_context

    @Property(bool, notify=preferencesChanged)
    def systemTextScaling(self) -> bool:
        return self._preferences.system_text_scaling

    @Property(int, notify=applicationFontPixelSizeChanged)
    def applicationFontPixelSize(self) -> int:
        application = QGuiApplication.instance()
        if not isinstance(application, QGuiApplication):
            return _DEFAULT_APPLICATION_FONT_PIXEL_SIZE
        pixel_size = QFontInfo(application.font()).pixelSize()
        return (
            pixel_size
            if pixel_size > 0
            else _DEFAULT_APPLICATION_FONT_PIXEL_SIZE
        )

    @Property(str, notify=persistenceChanged)
    def persistenceMessage(self) -> str:
        return self._persistence_message or "Saved"

    @Slot(bool)
    def setCompactDensity(self, enabled: bool) -> None:
        self._replace_preferences(compact_density=enabled)

    @Slot(bool)
    def setSecondaryStats(self, enabled: bool) -> None:
        self._replace_preferences(secondary_stats=enabled)

    @Slot(bool)
    def setCardPreview(self, enabled: bool) -> None:
        self._replace_preferences(card_preview=enabled)

    @Slot(bool)
    def setDetailedBuildContext(self, enabled: bool) -> None:
        self._replace_preferences(detailed_build_context=enabled)

    @Slot(bool)
    def setSystemTextScaling(self, enabled: bool) -> None:
        self._replace_preferences(system_text_scaling=enabled)

    def _replace_preferences(self, **changes: bool) -> None:
        updated = replace(self._preferences, **changes)
        if updated == self._preferences:
            return
        self._preferences = updated
        self._persistence_message = save_gui_preferences(
            preferences=updated,
            app_dir=self._app_dir,
        )
        self.preferencesChanged.emit()
        self.persistenceChanged.emit()


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

    @Slot(str)
    def chooseAccount(self, account_id: str) -> None:
        self._dispatch(command=ChooseAccount(account_id=account_id))

    @Slot(int)
    def chooseRecommendation(self, grp_id: int) -> None:
        self._dispatch(command=ChooseRecommendation(grp_id=grp_id))

    @Slot(int)
    def focusBuildCard(self, grp_id: int) -> None:
        self._dispatch(command=FocusBuildCard(grp_id=grp_id))

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
        set_code = ratings.get("set_code")
        if not set_code:
            return
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
            "message": "Draft Omen could not complete background work.",
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


class _CardImageFetchWorker(QObject):
    """Execute one session-owned card image fetch outside the session thread."""

    resultReady = Signal(object, object, str)

    def __init__(self, *, session: LiveSession) -> None:
        super().__init__()
        self._session = session

    @Slot(object)
    def fetch(self, request: CardImageRequest) -> None:
        try:
            result = self._session.fetch_card_image(request=request)
        except Exception as error:  # pragma: no cover - network boundary.
            self.resultReady.emit(request, None, str(error))
        else:
            self.resultReady.emit(request, result, "")


class _LiveSessionWorker(QObject):
    _imageFetchRequested = Signal(object)
    _imageScheduleRequested = Signal()
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
        self._stop_requested = False
        self._stopped = False
        self._startup_loading = False
        self._startup_snapshot: LiveSessionSnapshot | None = None
        self._image_thread: QThread | None = None
        self._image_worker: _CardImageFetchWorker | None = None
        self._image_request_in_flight: CardImageRequest | None = None
        self._image_request_kind: _ImageRequestKind | None = None

    def _publish_snapshot(self, snapshot: LiveSessionSnapshot) -> None:
        if self._stop_requested:
            return
        if self._startup_loading:
            self._startup_snapshot = snapshot
            return
        self.snapshotReady.emit(snapshot)

    def _flush_startup_snapshot(self) -> None:
        snapshot = self._startup_snapshot
        self._startup_snapshot = None
        self._startup_loading = False
        if snapshot is not None and not self._stop_requested:
            self.snapshotReady.emit(snapshot)

    def _start_image_worker(self) -> None:
        """Start the dedicated worker used for blocking image retrieval."""

        session = self._session
        if session is None or self._image_thread is not None:
            return
        thread = QThread(parent=self)
        image_worker = _CardImageFetchWorker(session=session)
        image_worker.moveToThread(thread)
        self._imageFetchRequested.connect(
            image_worker.fetch,
            Qt.ConnectionType.QueuedConnection,
        )
        # Queue completion so all LiveSession mutation stays on this worker.
        image_worker.resultReady.connect(
            self._image_fetch_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._imageScheduleRequested.connect(
            self._request_one_card_image,
            Qt.ConnectionType.QueuedConnection,
        )
        thread.finished.connect(image_worker.deleteLater)
        self._image_thread = thread
        self._image_worker = image_worker
        thread.start()


    def request_stop(self) -> None:
        """Request cooperative stop without queuing behind busy session work."""
        self._stop_requested = True

    @Slot()
    def start(self) -> None:
        self._startup_loading = True
        self._startup_snapshot = None
        try:
            self._session = self._session_factory(self._publish_snapshot)
            self._start_image_worker()
            self._publish_snapshot(self._session.snapshot)
            if self._stop_requested:
                self.stop()
                return
            if hasattr(self._session, "load_card_data"):
                self._session.load_card_data()
            if self._stop_requested:
                self.stop()
                return
            if self._startup_scan:
                self._session.scan_startup_files(
                    include_previous=True,
                    include_pre_draft_detection=False,
                )
            if self._stop_requested:
                self.stop()
                return
            initial_poll_succeeded = self._poll()
            if self._stop_requested:
                self.stop()
                return
            if initial_poll_succeeded:
                self._publish_snapshot(self._session.snapshot)
                self._flush_startup_snapshot()
            else:
                self._startup_snapshot = None
                self._startup_loading = False
            self._timer = QTimer(self)
            self._timer.setInterval(self._poll_interval_ms)
            self._timer.timeout.connect(self._poll)
            self._timer.start()
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            if not self._stop_requested:
                self.failed.emit(str(error))
            self.stop()

    @Slot(object)
    def dispatch(self, command: LiveSessionCommand) -> None:
        if self._session is None or self._stop_requested:
            return
        try:
            self._session.dispatch(command=command)
            self._request_one_card_image()
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.failed.emit(str(error))

    @Slot()
    def _poll(self) -> bool:
        if self._session is None:
            return False
        if self._stop_requested:
            self.stop()
            return False
        try:
            snapshot = self._session.poll_once()
            self._publish_snapshot(snapshot)
            self._request_one_card_image()
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.failed.emit(str(error))
            return False
        finally:
            if self._stop_requested:
                self.stop()
        return True

    def _request_one_card_image(self) -> None:
        """Schedule one image, prioritizing focus before pack thumbnails."""

        session = self._session
        if (
            session is None
            or self._stop_requested
            or self._image_request_in_flight is not None
            or self._image_thread is None
        ):
            return

        request: CardImageRequest | None = None
        request_kind: _ImageRequestKind | None = None
        for kind, method_name in (
            ("selected", "selected_card_image_request"),
            ("recommendation", "recommendation_image_request"),
            ("recent", "recent_pick_image_request"),
        ):
            get_request = getattr(session, method_name, None)
            if not callable(get_request):
                continue
            request = get_request()
            if request is not None:
                request_kind = cast(_ImageRequestKind, kind)
                break
        if request is None or request_kind is None or self._stop_requested:
            return

        self._image_request_in_flight = request
        self._image_request_kind = request_kind
        self._imageFetchRequested.emit(request)

    @Slot(object, object, str)
    def _image_fetch_finished(
        self,
        request: CardImageRequest,
        result: object,
        error_message: str,
    ) -> None:
        """Apply the result in the session thread and queue the next request."""
        if request != self._image_request_in_flight:
            return
        request_kind = self._image_request_kind
        self._image_request_in_flight = None
        self._image_request_kind = None
        session = self._session
        if session is None or self._stop_requested:
            return

        try:
            if error_message:
                if request_kind == "recommendation":
                    session.fail_recommendation_image_request(
                        request=request,
                        error_message=error_message,
                    )
                elif request_kind == "recent":
                    session.fail_recent_pick_image_request(
                        request=request,
                        error_message=error_message,
                    )
                else:
                    session.fail_card_image_request(
                        request=request,
                        error_message=error_message,
                    )
            else:
                if not isinstance(result, CardImageFetchResult):
                    raise TypeError("Card image worker returned an invalid result.")
                if request_kind == "recommendation":
                    session.complete_recommendation_image_request(
                        request=request,
                        image_path=result.image_path,
                        image_uri=result.image_uri,
                    )
                elif request_kind == "recent":
                    session.complete_recent_pick_image_request(
                        request=request,
                        image_path=result.image_path,
                        image_uri=result.image_uri,
                    )
                else:
                    session.complete_card_image_request(
                        request=request,
                        image_path=result.image_path,
                        image_uri=result.image_uri,
                    )
        except Exception as error:  # pragma: no cover - defensive UI boundary.
            self.failed.emit(str(error))
        finally:
            self._imageScheduleRequested.emit()

    @Slot()
    def stop(self) -> None:
        self._stop_requested = True
        self._startup_loading = False
        self._startup_snapshot = None
        if self._stopped:
            return
        if self._timer is not None:
            self._timer.stop()
        if self._session is not None:
            try:
                self._session.stop()
            except Exception as error:  # pragma: no cover - defensive UI boundary.
                self.failed.emit(str(error))
        image_thread = self._image_thread
        if image_thread is not None:
            image_thread.quit()
            image_thread.wait()
            self._image_thread = None
            self._image_worker = None
        self._stopped = True
        self.finished.emit()


class LiveSessionAdapter(SessionAdapter):
    """Run the production live session on one adapter-owned QThread.
    Immutable snapshots return through queued Qt signals.
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
        worker.finished.connect(
            thread.quit,
            Qt.ConnectionType.DirectConnection,
        )
        self.thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def shutdown(self) -> None:
        thread = self.thread
        worker = self._worker
        if thread is None or worker is None or not thread.isRunning():
            return
        worker.request_stop()
        thread.requestInterruption()
        QMetaObject.invokeMethod(
            worker,
            "stop",
            Qt.ConnectionType.QueuedConnection,
        )
        thread.wait(100)

    def wait_for_shutdown(self) -> None:
        """Wait for the owned worker thread before the adapter is destroyed."""
        thread = self.thread
        if thread is not None and thread.isRunning():
            thread.wait()

    def _dispatch(self, *, command: LiveSessionCommand) -> None:
        self._commandRequested.emit(command)
