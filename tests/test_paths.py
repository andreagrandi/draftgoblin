from pathlib import Path, PureWindowsPath

import pytest

from draftomen.paths import (
    UnsupportedPlatformError,
    app_data_dir,
    resolve_player_log_path,
)


def test_resolve_player_log_path_returns_macos_default_from_home() -> None:
    home = Path("/Users/example")

    log_path = resolve_player_log_path(home=home, system="Darwin")

    assert log_path == (
        home / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player.log"
    )


def test_resolve_player_log_path_returns_windows_default_from_home() -> None:
    home = PureWindowsPath("C:/Users/Arena")

    log_path = resolve_player_log_path(home=home, system="Windows")

    assert log_path == PureWindowsPath(
        "C:/Users/Arena/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"
    )


def test_resolve_player_log_path_honors_explicit_override() -> None:
    override = Path("/tmp/custom/Player.log")

    log_path = resolve_player_log_path(
        log_path=override,
        home=Path("/Users/example"),
        system="Darwin",
    )

    assert log_path == override


def test_resolve_player_log_path_requires_override_on_unsupported_platform() -> None:
    with pytest.raises(UnsupportedPlatformError):
        resolve_player_log_path(home=Path("/home/example"), system="Linux")


def test_app_data_dir_uses_current_user_home() -> None:
    assert app_data_dir(home=Path("/Users/example"), system="Darwin") == Path(
        "/Users/example/.draftomen"
    )
