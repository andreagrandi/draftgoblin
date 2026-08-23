"""Launch the deterministic PySide6 and QML desktop mockup.
The mock provider implements the same narrow contract as the live adapter.
"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import Property, QObject, Slot

from draftgoblin.mock_session import MOCK_SCENARIOS, MockLiveSession, MockScenario
from draftgoblin.qt_adapter import SessionAdapter
from draftgoblin.session import LiveSessionCommand


class MockSessionAdapter(SessionAdapter):
    """Translate deterministic snapshots and explicit commands for QML.
    The mock owns representative data only, never production behavior.
    """

    def __init__(
        self,
        *,
        session: MockLiveSession,
        parent: QObject | None = None,
    ) -> None:
        self._session = session
        super().__init__(snapshot=session.snapshot, parent=parent)

    @Property(bool, constant=True)
    def mockMode(self) -> bool:
        return True

    @Property(str, notify=SessionAdapter.scenarioChanged)
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
        self._publish(snapshot=self._session.snapshot)

    def _dispatch(self, *, command: LiveSessionCommand) -> None:
        self._session.dispatch(command=command)
        self._publish(snapshot=self._session.snapshot)


def main() -> int:
    from draftgoblin.qt_gui import run_gui

    return run_gui(forced_provider="mock")


if __name__ == "__main__":
    raise SystemExit(main())

