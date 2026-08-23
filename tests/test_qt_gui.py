from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from draftgoblin.audit import load_draft_audit_records
from draftgoblin.pool import load_draft_state


FIXTURE_ACCOUNT_ID = "FIXTURECLIENTID1234567890"
FIXTURE_DRAFT_ID = "00000000-0000-4000-8000-000000000004"
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURE_LOG_PATH = PROJECT_ROOT / "tests" / "fixtures" / "quick-draft-msh-player.log"
BULK_FILE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "scryfall-default-cards-sample.jsonl"


def test_production_gui_processes_representative_arena_log_offscreen(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    screenshot = tmp_path / "gui.png"
    environment = os.environ | {"QT_QPA_PLATFORM": "offscreen"}

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "draftgoblin.qt_gui",
            "--log-path",
            str(FIXTURE_LOG_PATH),
            "--app-dir",
            str(app_dir),
            "--bulk-file",
            str(BULK_FILE_PATH),
            "--poll-interval",
            "0.01",
            "--smoke-test-until-complete",
            "--screenshot",
            str(screenshot),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Binding loop detected" not in completed.stderr
    assert "Unable to assign" not in completed.stderr
    assert "TypeError" not in completed.stderr
    assert screenshot.is_file()
    assert load_draft_state(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    ).completed is True
    records = load_draft_audit_records(
        account_id=FIXTURE_ACCOUNT_ID,
        draft_id=FIXTURE_DRAFT_ID,
        app_dir=app_dir,
    )
    assert records[-1]["record_type"] == "draft_completed"


def test_qml_keyboard_controls_dispatch_account_and_ratings_commands_offscreen() -> None:
    probe = """
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtQuickControls2 import QQuickStyle

from draftgoblin.mock_session import MockLiveSession
from draftgoblin.qt_mock import MockSessionAdapter
from draftgoblin.session import ChooseAccount, RequestRatingsDownload


class RecordingProvider(MockSessionAdapter):
    def __init__(self) -> None:
        self.commands = []
        super().__init__(session=MockLiveSession(scenario="warning"))

    def _dispatch(self, *, command) -> None:
        self.commands.append(command)
        super()._dispatch(command=command)


QQuickStyle.setStyle("Fusion")
application = QGuiApplication([])
provider = RecordingProvider()
engine = QQmlApplicationEngine()
qml_directory = Path.cwd() / "draftgoblin" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draftgoblin")
context.setContextProperty("initialSurface", "live")
context.setContextProperty("initialWindowWidth", 1440)
context.setContextProperty("initialWindowHeight", 900)
engine.setInitialProperties({"provider": provider})
engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
root = engine.rootObjects()[0]
application.processEvents()

account_selector = root.findChild(QObject, "accountSelector")
assert account_selector is not None
account_selector.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
QTest.keyClick(root, Qt.Key_Return)
application.processEvents()
assert any(isinstance(command, ChooseAccount) for command in provider.commands)

download = root.findChild(QObject, "ratingsDownloadButton")
assert download is not None
download.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
dialog = root.findChild(QObject, "ratingsDownloadDialog")
assert dialog is not None
assert dialog.property("visible") is True
QTest.keyClick(root, Qt.Key_Escape)
application.processEvents()
assert dialog.property("visible") is False

settings = root.findChild(QObject, "settingsButton")
assert settings is not None
settings.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
settings_download = root.findChild(QObject, "settingsRatingsDownloadButton")
assert settings_download is not None
settings_download.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
settings_dialog = root.findChild(QObject, "settingsRatingsDownloadDialog")
assert settings_dialog is not None
assert settings_dialog.property("visible") is True
settings_cancel = root.findChild(QObject, "settingsRatingsDownloadCancelButton")
assert settings_cancel is not None
settings_cancel.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert settings_dialog.property("visible") is False

settings_download.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert settings_dialog.property("visible") is True
QTest.keyClick(root, Qt.Key_Escape)
application.processEvents()
assert settings_dialog.property("visible") is False

settings_download.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert settings_dialog.property("visible") is True
settings_confirm = root.findChild(QObject, "settingsRatingsDownloadConfirmButton")
assert settings_confirm is not None
settings_confirm.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert any(
    isinstance(command, RequestRatingsDownload) for command in provider.commands
)
assert provider.state["ratings"]["phase"] == "loading"
"""
    environment = os.environ | {"QT_QPA_PLATFORM": "offscreen"}

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

