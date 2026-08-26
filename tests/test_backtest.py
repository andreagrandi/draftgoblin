from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from draftomen.backtest import format_backtest_report, generate_backtest_report
from draftomen.carddb import CardDatabase, CardInfo
from draftomen.cli import main
from draftomen.pool import DraftPick, DraftState, draft_state_path, save_draft_state

FIXTURE_NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC).isoformat()


def test_backtest_uses_saved_pool_before_pick_for_recommendation() -> None:
    state = _draft_state(
        picks=(
            DraftPick(
                pack_number=0,
                pick_number=0,
                offered_grp_ids=(4, 3),
                pool_before_pick=(),
                chosen_grp_id=3,
            ),
            DraftPick(
                pack_number=0,
                pick_number=5,
                offered_grp_ids=(4, 3),
                pool_before_pick=(1, 2),
                chosen_grp_id=3,
            ),
        ),
        pool_grp_ids=(4, 4, 4),
    )

    report = generate_backtest_report(
        state=state,
        card_database=_card_database(),
    )
    output = format_backtest_report(report)

    assert report.rows[0].recommended is not None
    assert report.rows[0].recommended.card.grp_id == 4
    assert report.rows[0].actual is not None
    assert report.rows[0].actual.grp_id == 3
    assert report.rows[0].match is False
    assert report.rows[1].recommended is not None
    assert report.rows[1].recommended.card.grp_id == 3
    assert report.rows[1].pool_size == 2
    assert report.rows[1].offered_count == 2
    assert report.rows[1].match is True
    assert "Ranking: DO Score" in output
    assert "Data sources: neutral prior" in output
    assert "Red Temptation [R] (grpId 4)" in output
    assert "White Followup [W] (grpId 3)" in output
    assert "Summary: 1/2 recommendations matched actual picks (50.0%)." in output


def test_backtest_cli_skips_missing_offered_history_without_mutating_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "app"
    bulk_file = _write_bulk_file(directory=tmp_path)
    state = _draft_state(
        picks=(
            DraftPick(
                pack_number=0,
                pick_number=0,
                offered_grp_ids=None,
                pool_before_pick=(),
                chosen_grp_id=3,
            ),
        ),
        pool_grp_ids=(3,),
    )
    save_draft_state(state=state, app_dir=app_dir)
    state_path = draft_state_path(
        account_id=state.account_id,
        draft_id=state.draft_id,
        app_dir=app_dir,
    )
    before = state_path.read_text(encoding="utf-8")

    exit_code = main(
        argv=[
            "backtest",
            "--account",
            state.account_id,
            "--draft-id",
            state.draft_id,
            "--bulk-file",
            str(bulk_file),
            "--app-dir",
            str(app_dir),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Draft Omen backtest" in captured.out
    assert "Ranking: DO Score" in captured.out
    assert "skipped: missing offered-card history" in captured.out
    assert "Summary: no comparable picks; 1 skipped." in captured.out
    assert captured.err == ""
    assert state_path.read_text(encoding="utf-8") == before


def _draft_state(
    *,
    picks: tuple[DraftPick, ...],
    pool_grp_ids: tuple[int, ...],
) -> DraftState:
    return DraftState(
        account_id="acct",
        account_screen_name="Tester",
        draft_id="draft",
        event_name="QuickDraft_TST_20260703",
        set_code="TST",
        course_id="draft",
        started_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        completed_at=FIXTURE_NOW,
        completed=True,
        picks=picks,
        pool_grp_ids=pool_grp_ids,
    )


def _card_database() -> CardDatabase:
    return CardDatabase(
        cards={
            1: _card(grp_id=1, name="White Prior", colors=("W",)),
            2: _card(grp_id=2, name="Blue Prior", colors=("U",)),
            3: _card(grp_id=3, name="White Followup", colors=("W",)),
            4: _card(grp_id=4, name="Red Temptation", colors=("R",)),
        }
    )


def _card(*, grp_id: int, name: str, colors: tuple[str, ...]) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=colors,
        mana_value=2.0,
        rarity="common",
        types=("Creature",),
    )


def _write_bulk_file(*, directory: Path) -> Path:
    path = directory / "backtest-bulk.jsonl"
    rows = [
        _scryfall_row(grp_id=1, name="White Prior", colors=["W"]),
        _scryfall_row(grp_id=2, name="Blue Prior", colors=["U"]),
        _scryfall_row(grp_id=3, name="White Followup", colors=["W"]),
        _scryfall_row(grp_id=4, name="Red Temptation", colors=["R"]),
    ]
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _scryfall_row(*, grp_id: int, name: str, colors: list[str]) -> dict[str, object]:
    return {
        "arena_id": grp_id,
        "name": name,
        "colors": colors,
        "cmc": 2,
        "rarity": "common",
        "type_line": "Creature — Fixture",
    }

