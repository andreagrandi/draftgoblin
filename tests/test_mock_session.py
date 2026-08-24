from __future__ import annotations

import pytest

from draftgoblin.mock_session import MOCK_SCENARIOS, MockLiveSession
from draftgoblin.session import (
    ApplicationPhase,
    ChangeRanking,
    ChangeSplashPreference,
    ChooseRecommendation,
    DataLoadPhase,
    DismissError,
    FocusBuildCard,
    RequestBacktest,
    RequestBuild,
    RequestRatingsDownload,
    RetryError,
)


def test_ready_mock_snapshot_covers_every_desktop_data_surface() -> None:
    snapshot = MockLiveSession().snapshot

    assert snapshot.status.phase == ApplicationPhase.DRAFTING
    assert snapshot.recommendations.cards
    assert snapshot.recommendations.selected_grp_id is not None
    assert snapshot.recommendations.cards[0].letter_grade == "A-"
    assert snapshot.recommendations.cards[0].explanation
    assert snapshot.recommendations.confidence_summary is None
    assert snapshot.pool.total_cards == 10
    assert snapshot.pool.target_cards == 42
    assert sum(
        pool_card.quantity for pool_card in snapshot.pool.cards
    ) == snapshot.pool.total_cards
    assert all(
        pool_card.quantity == 1 for pool_card in snapshot.pool.recent_picks
    )
    assert snapshot.pool.color_distribution == (
        ("W", 0),
        ("U", 0),
        ("B", 1),
        ("R", 0),
        ("G", 9),
        ("C", 0),
    )
    assert sum(snapshot.pool.mana_curve) == snapshot.pool.total_cards
    assert snapshot.build is not None
    assert snapshot.build.deck_size == 40
    assert snapshot.build.spell_count == 23
    assert snapshot.build.land_count == 17
    assert snapshot.backtest is not None
    assert snapshot.backtest.rows


@pytest.mark.parametrize("scenario", MOCK_SCENARIOS)
def test_mock_scenarios_are_repeatable(scenario: str) -> None:
    session = MockLiveSession()

    first = session.select_scenario(scenario=scenario)  # type: ignore[arg-type]
    second = session.select_scenario(scenario=scenario)  # type: ignore[arg-type]

    assert first == second
    assert session.scenario == scenario


def test_mock_scenarios_cover_loading_empty_progress_warning_and_error() -> None:
    session = MockLiveSession(scenario="loading")

    assert session.snapshot.progress is not None
    assert session.snapshot.progress.total is None

    empty = session.select_scenario(scenario="empty")
    assert empty.status.phase == ApplicationPhase.WAITING_FOR_DRAFT
    assert empty.draft is None
    assert empty.recommendations.cards == ()

    progress = session.select_scenario(scenario="progress")
    assert progress.progress is not None
    assert progress.progress.completed == 340
    assert progress.progress.total == 1000

    warning = session.select_scenario(scenario="warning")
    assert warning.ratings.phase.value == "missing"
    assert warning.build is not None
    assert warning.build.warnings

    error = session.select_scenario(scenario="error")
    assert error.errors[0].recoverable is True


def test_mock_provider_dispatches_production_commands() -> None:
    session = MockLiveSession()
    selected_grp_id = session.snapshot.recommendations.cards[2].card.grp_id
    build = session.snapshot.build
    assert build is not None
    session.dispatch(command=FocusBuildCard(grp_id=build.spells[0].card.grp_id))

    selected = session.dispatch(
        command=ChooseRecommendation(grp_id=selected_grp_id),
    )
    assert selected.recommendations.selected_grp_id == selected_grp_id
    assert selected.card_image.grp_id == selected_grp_id
    assert selected.card_image.image_path is None
    assert selected.card_image.phase == DataLoadPhase.UNAVAILABLE

    ranked = session.dispatch(command=ChangeRanking(ranking_mode="alsa"))
    assert ranked.recommendations.ranking_mode == "alsa"
    assert tuple(
        card.average_last_seen_at for card in ranked.recommendations.cards
    ) == tuple(
        sorted(
            card.average_last_seen_at for card in ranked.recommendations.cards
        )
    )

    splash = session.dispatch(command=ChangeSplashPreference(enabled=False))
    assert splash.recommendations.splash_enabled is False

    build = session.dispatch(
        command=RequestBuild(pair_override="BG", allow_splash=False),
    )
    assert build.build is not None
    assert build.build.pair_override == "BG"

    backtest = session.dispatch(command=RequestBacktest())
    assert backtest.backtest is not None

    progress = session.dispatch(command=RequestRatingsDownload(set_code="OTJ"))
    assert progress.progress is not None
    assert session.scenario == "progress"


def test_mock_provider_retries_and_dismisses_errors_by_identifier() -> None:
    session = MockLiveSession(scenario="error")
    error_id = session.snapshot.errors[0].error_id

    dismissed = session.dispatch(command=DismissError(error_id=error_id))
    assert dismissed.errors == ()

    session.select_scenario(scenario="error")
    retried = session.dispatch(command=RetryError(error_id=error_id))
    assert retried.errors == ()
    assert session.scenario == "ready"


def test_mock_provider_rejects_unknown_scenario() -> None:
    session = MockLiveSession()

    with pytest.raises(ValueError, match="Unsupported mock scenario"):
        session.select_scenario(scenario="unknown")  # type: ignore[arg-type]
