from __future__ import annotations

import json
from pathlib import Path

from draftgoblin.logfollow import LogFollower, log_offset_path


def test_partial_lines_are_buffered_and_offset_resumes_across_restarts(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    log_path.write_text("first\nsecond", encoding="utf-8")
    follower = LogFollower(
        log_path=log_path,
        app_dir=app_dir,
        poll_interval=0.25,
    )

    assert follower.poll_interval == 0.25
    assert follower.poll() == ("first",)

    offset_path = log_offset_path(log_path=log_path, app_dir=app_dir)
    assert offset_path.parent == app_dir / "logfollow"
    assert offset_path.exists()
    state = json.loads(offset_path.read_text(encoding="utf-8"))
    assert state["offset"] == len("first\n")

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write("\nthird\n")

    restarted = LogFollower(log_path=log_path, app_dir=app_dir)

    assert restarted.poll() == ("second", "third")
    assert restarted.poll() == ()


def test_truncation_resets_offset_without_replaying_old_lines(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    log_path.write_text("old one\nold two\n", encoding="utf-8")
    follower = LogFollower(log_path=log_path, app_dir=app_dir)

    assert follower.poll() == ("old one", "old two")

    log_path.write_text("new\n", encoding="utf-8")

    assert follower.poll() == ("new",)
    assert follower.poll() == ()


def test_recreated_log_resets_offset_without_replaying_old_lines(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    log_path.write_text("old\n", encoding="utf-8")
    follower = LogFollower(log_path=log_path, app_dir=app_dir)

    assert follower.poll() == ("old",)

    log_path.unlink()
    log_path.write_text("new\n", encoding="utf-8")

    assert follower.poll() == ("new",)
    assert follower.poll() == ()


def test_rotation_to_player_prev_recovers_unread_tail_once(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    previous_log_path = tmp_path / "Player-prev.log"
    log_path.write_text("one\n", encoding="utf-8")
    follower = LogFollower(log_path=log_path, app_dir=app_dir)

    assert follower.poll() == ("one",)

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write("two\n")
    log_path.rename(previous_log_path)
    log_path.write_text("three\n", encoding="utf-8")

    assert follower.poll() == ("two", "three")
    assert follower.poll() == ()


def test_startup_scan_reads_player_prev_then_current_and_advances_offset(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    log_path = tmp_path / "Player.log"
    previous_log_path = tmp_path / "Player-prev.log"
    previous_log_path.write_text("prev one\nprev two\n", encoding="utf-8")
    log_path.write_text("current one\ncurrent two", encoding="utf-8")
    follower = LogFollower(log_path=log_path, app_dir=app_dir)

    assert follower.scan_startup_files() == ("prev one", "prev two", "current one")

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write("\ncurrent three\n")

    assert follower.poll() == ("current two", "current three")
    assert follower.poll() == ()

