from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from draftgoblin.mock_session import MOCK_SCENARIOS, MockLiveSession
from draftgoblin.qt_adapter import _to_qml_value
from draftgoblin.qt_mock import MockSessionAdapter
from draftgoblin.session import LiveSessionSnapshot


def test_qt_translation_publishes_plain_values_without_domain_payloads() -> None:
    values = _to_qml_value(MockLiveSession().snapshot)

    assert values["status"]["phase"] == "drafting"
    assert values["recommendations"]["cards"][0]["card"]["name"]
    assert "domain_pool" not in values["build"]
    assert "domain_selection" not in values["build"]


def test_qt_mock_adapter_exposes_shared_provider_contract() -> None:
    session = MockLiveSession()
    adapter = MockSessionAdapter(session=session)

    assert adapter.mockMode is True
    assert adapter.scenario == "ready"
    assert adapter.scenarios == list(MOCK_SCENARIOS)
    assert adapter.recommendationsModel.rowCount() == len(
        session.snapshot.recommendations.cards
    )

    adapter.selectScenario("warning")
    assert adapter.scenario == "warning"
    assert adapter.state["ratings"]["phase"] == "missing"

    selected_grp_id = session.snapshot.recommendations.cards[1].card.grp_id
    adapter.chooseRecommendation(selected_grp_id)
    assert adapter.state["recommendations"]["selected_grp_id"] == selected_grp_id

    adapter.changeRanking("win_rate")
    assert adapter.state["recommendations"]["ranking_mode"] == "win_rate"

    adapter.setSplashEnabled(False)
    assert adapter.state["recommendations"]["splash_enabled"] is False

    adapter.requestBuild("BG")
    assert adapter.state["build"]["pair_override"] == "BG"

    adapter.requestBacktest()
    assert adapter.state["backtest"]["rows"]

def test_qt_adapter_ignores_unknown_visual_scenario() -> None:
    adapter = MockSessionAdapter(session=MockLiveSession())

    adapter.selectScenario("unknown")

    assert adapter.scenario == "ready"
    assert isinstance(adapter.state, dict)
    assert isinstance(_to_qml_value(LiveSessionSnapshot()), dict)


