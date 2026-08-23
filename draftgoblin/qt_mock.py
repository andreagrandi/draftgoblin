"""Launch the deterministic PySide6 and QML desktop mockup.
Qt-specific translation remains isolated from the shared session contract.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from draftgoblin.mock_session import MOCK_SCENARIOS, MockLiveSession, MockScenario
from draftgoblin.ranking import RankingMode
from draftgoblin.session import (
    ChangeRanking,
    ChangeSplashPreference,
    ChooseRecommendation,
    DismissError,
    LiveSessionCommand,
    RequestBacktest,
    RequestBuild,
    RequestRatingsDownload,
    RetryError,
)

SURFACES = ("live", "build", "backtest", "settings")


def _to_qml_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_qml_value(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("domain_")
        }
    if isinstance(value, tuple):
        return [_to_qml_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _to_qml_value(item)
            for key, item in value.items()
        }
    return value


class MockSessionAdapter(QObject):
    """Translate immutable snapshots and explicit commands for QML.
    The adapter publishes plain Qt values and owns no domain behavior.
    """

    stateChanged = Signal()
    scenarioChanged = Signal()

    def __init__(
        self,
        *,
        session: MockLiveSession,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._state = _to_qml_value(session.snapshot)

    @Property("QVariantMap", notify=stateChanged)
    def state(self) -> dict[str, Any]:
        return self._state

    @Property(str, notify=scenarioChanged)
    def scenario(self) -> str:
        return self._session.scenario

    @Property("QStringList", constant=True)
    def scenarios(self) -> list[str]:
        return list(MOCK_SCENARIOS)

    @Slot(str)
    def selectScenario(self, scenario: str) -> None:
        if scenario not in MOCK_SCENARIOS:
            return
        self._session.select_scenario(scenario=cast(MockScenario, scenario))
        self.scenarioChanged.emit()
        self._publish()

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
        set_code = self._session.snapshot.ratings.set_code or "OTJ"
        self._dispatch(command=RequestRatingsDownload(set_code=set_code))
        self.scenarioChanged.emit()

    @Slot(str)
    def requestBuild(self, pair_override: str) -> None:
        self._dispatch(
            command=RequestBuild(
                pair_override=pair_override or None,
                allow_splash=self._session.snapshot.recommendations.splash_enabled,
            )
        )

    @Slot()
    def requestBacktest(self) -> None:
        self._dispatch(command=RequestBacktest())

    @Slot(str)
    def dismissError(self, error_id: str) -> None:
        self._dispatch(command=DismissError(error_id=error_id))

    @Slot(str)
    def retryError(self, error_id: str) -> None:
        self._dispatch(command=RetryError(error_id=error_id))
        self.scenarioChanged.emit()

    def _dispatch(self, *, command: LiveSessionCommand) -> None:
        self._session.dispatch(command=command)
        self._publish()

    def _publish(self) -> None:
        self._state = _to_qml_value(self._session.snapshot)
        self.stateChanged.emit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch the deterministic Draftgoblin QML mockup.",
    )
    parser.add_argument(
        "--scenario",
        choices=MOCK_SCENARIOS,
        default="ready",
        help="Initial representative state.",
    )
    parser.add_argument(
        "--surface",
        choices=SURFACES,
        default="live",
        help="Initial application surface.",
    )
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Render the window and exit automatically.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Save the rendered window before exiting.",
    )
    return parser


def _finish_smoke_test(
    *,
    engine: QQmlApplicationEngine,
    application: QGuiApplication,
    screenshot: Path | None,
) -> None:
    root_objects = engine.rootObjects()
    if not root_objects:
        application.exit(1)
        return
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        image = root_objects[0].grabWindow()
        if image.isNull() or not image.save(str(screenshot)):
            application.exit(1)
            return
        print(f"Saved mockup screenshot to {screenshot}")
    application.quit()


def main() -> int:
    args = _parser().parse_args()
    QQuickStyle.setStyle("Fusion")
    application = QGuiApplication(sys.argv)
    application.setApplicationName("Draftgoblin QML Mockup")
    application.setOrganizationName("Draftgoblin")

    engine = QQmlApplicationEngine()
    qml_directory = Path(__file__).with_name("qml")
    engine.addImportPath(str(qml_directory))

    session = MockLiveSession(scenario=cast(MockScenario, args.scenario))
    adapter = MockSessionAdapter(session=session)
    context = engine.rootContext()
    context.setContextProperty("mockProvider", adapter)
    context.setContextProperty("initialSurface", args.surface)
    context.setContextProperty("initialWindowWidth", args.width)
    context.setContextProperty("initialWindowHeight", args.height)

    engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
    if not engine.rootObjects():
        return 1

    if args.smoke_test or args.screenshot is not None:
        QTimer.singleShot(
            800,
            lambda: _finish_smoke_test(
                engine=engine,
                application=application,
                screenshot=args.screenshot,
            ),
        )
    exit_code = application.exec()
    del engine
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


