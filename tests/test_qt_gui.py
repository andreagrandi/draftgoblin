from __future__ import annotations

import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from draftomen import __version__
from draftomen.audit import load_draft_audit_records
from draftomen.carddb import CardDatabase
from draftomen.pool import load_draft_state
from draftomen.qt_gui import (
    APPLICATION_NAME,
    _configure_application_metadata,
    _live_session_factory,
)


FIXTURE_ACCOUNT_ID = "FIXTURECLIENTID1234567890"
FIXTURE_DRAFT_ID = "00000000-0000-4000-8000-000000000004"
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURE_LOG_PATH = PROJECT_ROOT / "tests" / "fixtures" / "quick-draft-msh-player.log"
BULK_FILE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "scryfall-default-cards-sample.jsonl"


def _run_qml_probe(
    probe: str,
    *,
    timeout: int = 15,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ | {"QT_QPA_PLATFORM": "offscreen"}
    if environment is not None:
        process_environment.update(environment)
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=process_environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_gui_metadata_uses_canonical_version_and_logo_resource() -> None:
    class RecordingApplication:
        def __init__(self) -> None:
            self.metadata: dict[str, str] = {}

        def setApplicationName(self, value: str) -> None:
            self.metadata["name"] = value

        def setApplicationDisplayName(self, value: str) -> None:
            self.metadata["display_name"] = value

        def setApplicationVersion(self, value: str) -> None:
            self.metadata["version"] = value

        def setOrganizationName(self, value: str) -> None:
            self.metadata["organization"] = value

    application = RecordingApplication()
    _configure_application_metadata(application=application)  # type: ignore[arg-type]

    assert application.metadata == {
        "name": APPLICATION_NAME,
        "display_name": APPLICATION_NAME,
        "version": __version__,
        "organization": APPLICATION_NAME,
    }
    source_logo = PROJECT_ROOT / "docs" / "assets" / "draftomen_logo.png"
    runtime_logo = files("draftomen").joinpath("assets/draftomen_logo.png")
    assert runtime_logo.is_file()
    assert runtime_logo.read_bytes() == source_logo.read_bytes()


def test_live_gui_uses_shared_metadata_augmenting_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = CardDatabase(cards={})
    factory_calls: list[tuple[CardDatabase, Path | None, bool]] = []

    monkeypatch.setattr(
        "draftomen.qt_gui.load_or_refresh_card_database",
        lambda *, app_dir: database,
    )

    def shared_loader_factory(
        *,
        database: CardDatabase,
        load_ratings: object,
        app_dir: Path | None,
        persist_database: bool,
    ) -> object:
        del load_ratings
        factory_calls.append((database, app_dir, persist_database))
        return lambda set_code, progress_callback: None

    monkeypatch.setattr(
        "draftomen.qt_gui.metadata_augmenting_ratings_progress_loader",
        shared_loader_factory,
    )
    app_dir = tmp_path / "app"
    session_factory = _live_session_factory(
        log_path=tmp_path / "Player.log",
        app_dir=app_dir,
        bulk_file=None,
        poll_interval=0.01,
    )
    session = session_factory(lambda snapshot: None)

    session.load_card_data()

    assert factory_calls == [(database, app_dir, True)]


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
            "draftomen.qt_gui",
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
    assert "Monospace" not in completed.stderr
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


def test_production_adapter_renders_build_backtest_and_persisted_preferences_offscreen(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    probe = """
import json
import os
import time
import urllib.parse
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from draftomen import __version__
from draftomen.carddb import build_card_database_from_bulk_file
from draftomen.cardimages import CardImageService
from draftomen.qt_adapter import GuiPreferencesAdapter, LiveSessionAdapter
from draftomen.qt_gui import _fixed_font_family
from draftomen.session import LiveSession


def find_visual_item(item: QQuickItem, object_name: str) -> QQuickItem | None:
    if item.objectName() == object_name:
        return item
    for child in item.childItems():
        found = find_visual_item(child, object_name)
        if found is not None:
            return found
    return None

def image_source(image: QObject) -> str:
    source = image.property("source")
    return source.toString() if isinstance(source, QUrl) else str(source)


def wait_until(predicate, description: str) -> None:
    deadline = time.monotonic() + 8
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for " + description)
        time.sleep(0.005)
    application.processEvents()


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def read(self, maximum: int) -> bytes:
        return self.payload[:maximum]


def metadata_opener(request, timeout):
    del timeout
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    exact_name = query["exact"][0]
    image_uri = (
        "https://images.example/"
        + urllib.parse.quote(exact_name, safe="")
        + ".png"
    )
    return _Response(
        json.dumps({"image_uris": {"normal": image_uri}}).encode("utf-8")
    )


def image_opener(request, timeout):
    del request, timeout
    return _Response(
        (project_root / "draftomen" / "assets" / "draftomen_logo.png").read_bytes()
    )


def load_image_database():
    database = build_card_database_from_bulk_file(path=bulk_file)
    return replace(
        database,
        cards={
            grp_id: replace(card, image_uri=None)
            for grp_id, card in database.cards.items()
        },
        image_uris_by_name={},
    )


project_root = Path.cwd()
app_dir = Path(os.environ["DRAFTOMEN_E2E_APP_DIR"])
fixture_log_path = project_root / "tests" / "fixtures" / "quick-draft-msh-player.log"
fixture_log_lines = fixture_log_path.read_text(encoding="utf-8").splitlines(keepends=True)
log_path = app_dir / "Player.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
log_path.write_text("".join(fixture_log_lines[:22]), encoding="utf-8")
bulk_file = project_root / "tests" / "fixtures" / "scryfall-default-cards-sample.jsonl"


def factory(publish):
    return LiveSession(
        log_path=log_path,
        card_database_loader=load_image_database,
        app_dir=app_dir,
        poll_interval=0.01,
        snapshot_publisher=publish,
        card_image_service=CardImageService(
            cache_dir=app_dir / "card-images",
            opener=image_opener,
            metadata_opener=metadata_opener,
            monotonic_clock=lambda: 0.0,
            sleep=lambda seconds: None,
        ),
        ratings_cache_checker=lambda _set_code: False,
    )


QQuickStyle.setStyle("Fusion")
application = QGuiApplication([])
provider = LiveSessionAdapter(session_factory=factory, poll_interval_ms=10)
preferences = GuiPreferencesAdapter(app_dir=app_dir)
engine = QQmlApplicationEngine()
qml_directory = project_root / "draftomen" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("fixedFontFamily", _fixed_font_family())
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draft Omen")
context.setContextProperty("applicationVersion", __version__)
context.setContextProperty("guiPreferences", preferences)
context.setContextProperty("initialSurface", "live")
context.setContextProperty("initialWindowWidth", 1440)
context.setContextProperty("initialWindowHeight", 900)
engine.setInitialProperties({"provider": provider})
engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
root = engine.rootObjects()[0]
provider.start()
try:
    assert root.property("currentSurface") == "live"
    root.resize(760, 900)
    application.processEvents()
    live_preview = root.findChild(QObject, "narrowLiveCardPreview")
    assert live_preview is not None
    live_image = live_preview.findChild(QObject, "cardPreviewImage")
    assert live_image is not None
    wait_until(
        lambda: provider.state["card_image"]["phase"] == "ready"
        and live_preview.property("imageCurrent") is True
        and live_image.isVisible(),
        "the visible selected recommendation image",
    )
    first_recommendation_source = image_source(live_image)
    first_recommendation_grp_id = provider.state["card_image"]["grp_id"]
    next_recommendation_grp_id = next(
        card["card"]["grp_id"]
        for card in provider.state["recommendations"]["cards"]
        if card["card"]["grp_id"] != first_recommendation_grp_id
    )
    provider.chooseRecommendation(next_recommendation_grp_id)
    wait_until(
        lambda: provider.state["card_image"]["grp_id"] == next_recommendation_grp_id
        and provider.state["card_image"]["phase"] == "ready"
        and live_preview.property("imageCurrent") is True
        and live_image.isVisible()
        and image_source(live_image) != first_recommendation_source,
        "the visible changed recommendation image",
    )
    next_recommendation_source = image_source(live_image)
    assert next_recommendation_source
    provider.requestBuild("")
    wait_until(
        lambda: provider.state.get("build") is not None,
        "the published active-draft build",
    )


    root.setProperty("currentSurface", "build")
    assert provider.state["build"]["spells"]
    build_spell = find_visual_item(root.contentItem(), "buildSpellButton1")
    assert build_spell is not None and build_spell.isVisible()
    build_spell.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Space)
    build_preview = root.findChild(QObject, "narrowBuildCardPreview")
    assert build_preview is not None
    build_image = build_preview.findChild(QObject, "cardPreviewImage")
    assert build_image is not None
    first_build_grp_id = provider.state["build"]["spells"][1]["card"]["grp_id"]
    wait_until(
        lambda: provider.state["card_image"]["grp_id"] == first_build_grp_id
        and provider.state["card_image"]["phase"] == "ready"
        and build_preview.property("imageCurrent") is True
        and build_image.isVisible(),
        "the visible focused build image",
    )
    first_build_source = image_source(build_image)
    next_build_spell = find_visual_item(root.contentItem(), "buildSpellButton2")
    assert next_build_spell is not None and next_build_spell.isVisible()
    next_build_spell.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Space)
    next_build_grp_id = provider.state["build"]["spells"][2]["card"]["grp_id"]
    wait_until(
        lambda: provider.state["card_image"]["grp_id"] == next_build_grp_id
        and provider.state["card_image"]["phase"] == "ready"
        and build_preview.property("imageCurrent") is True
        and build_image.isVisible()
        and image_source(build_image) != first_build_source,
        "the visible changed build image",
    )
    next_build_source = image_source(build_image)
    assert next_build_source

    root.setProperty("currentSurface", "live")
    wait_until(
        lambda: provider.state["card_image"]["grp_id"] == next_recommendation_grp_id
        and provider.state["card_image"]["phase"] == "ready"
        and live_preview.property("imageCurrent") is True
        and live_image.isVisible()
        and image_source(live_image) == next_recommendation_source,
        "the restored visible recommendation image",
    )

    root.setProperty("currentSurface", "build")
    wait_until(
        lambda: provider.state["card_image"]["grp_id"] == next_build_grp_id
        and provider.state["card_image"]["phase"] == "ready"
        and build_preview.property("imageCurrent") is True
        and build_image.isVisible()
        and image_source(build_image) == next_build_source,
        "the restored visible build image",
    )
    with log_path.open(mode="a", encoding="utf-8") as log_file:
        log_file.writelines(fixture_log_lines[22:])
    wait_until(
        lambda: (provider.state.get("draft") or {}).get("completed") is True,
        "the completed representative draft",
    )


    root.setProperty("currentSurface", "backtest")
    application.processEvents()
    run_backtest = root.findChild(QObject, "backtestRunButton")
    assert run_backtest is not None and run_backtest.property("visible") is True
    run_backtest.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Space)
    wait_until(
        lambda: provider.state.get("backtest") is not None,
        "the published production backtest",
    )
    assert provider.state["backtest"]["rows"]
    backtest_rows = root.findChild(QObject, "backtestRows")
    assert backtest_rows is not None and backtest_rows.property("count") > 0

    root.setProperty("currentSurface", "settings")
    application.processEvents()
    card_preview = root.findChild(QObject, "settingsCardPreviewSwitch")
    assert card_preview is not None and card_preview.property("checked") is True
    card_preview.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Space)
    application.processEvents()
    assert preferences.cardPreview is False
    assert GuiPreferencesAdapter(app_dir=app_dir).cardPreview is False
finally:
    provider.shutdown()
    provider.wait_for_shutdown()
    del engine
"""
    completed = _run_qml_probe(
        probe,
        timeout=20,
        environment={"DRAFTOMEN_E2E_APP_DIR": str(app_dir)},
    )

    assert completed.returncode == 0, completed.stderr
    assert "Binding loop detected" not in completed.stderr
    assert "Unable to assign" not in completed.stderr
    assert "TypeError" not in completed.stderr


def test_completed_draft_automatically_builds_and_survives_unrelated_snapshots() -> None:
    probe = """
from dataclasses import replace
from pathlib import Path
import time
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from draftomen.mock_session import MockLiveSession
from draftomen.qt_adapter import GuiPreferencesAdapter
from draftomen.qt_mock import MockSessionAdapter
from draftomen.session import ApplicationPhase


def wait_until(predicate, description):
    deadline = time.monotonic() + 5
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for " + description)
        time.sleep(0.005)
    application.processEvents()


QQuickStyle.setStyle("Fusion")
application = QGuiApplication([])
session = MockLiveSession(scenario="ready")
session._snapshot = replace(
    session.snapshot,
    status=replace(
        session.snapshot.status,
        phase=ApplicationPhase.DRAFT_COMPLETE,
        message="Draft complete.",
    ),
    draft=replace(session.snapshot.draft, completed=True),
    pool=replace(session.snapshot.pool, total_cards=42),
    build=None,
)
provider = MockSessionAdapter(session=session)
with TemporaryDirectory() as preferences_dir:
    preferences = GuiPreferencesAdapter(
        app_dir=preferences_dir,
        parent=application,
    )
    engine = QQmlApplicationEngine()
    qml_directory = Path.cwd() / "draftomen" / "qml"
    engine.addImportPath(str(qml_directory))
    context = engine.rootContext()
    context.setContextProperty("fixedFontFamily", "monospace")
    context.setContextProperty("sessionProvider", provider)
    context.setContextProperty("applicationTitle", "Draft Omen")
    context.setContextProperty("applicationVersion", "0.0")
    context.setContextProperty("guiPreferences", preferences)
    context.setContextProperty("initialSurface", "build")
    context.setContextProperty("initialWindowWidth", 1440)
    context.setContextProperty("initialWindowHeight", 900)
    engine.setInitialProperties({"provider": provider})
    engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
    root = engine.rootObjects()[0]
    build_view = root.findChild(QObject, "buildView")
    assert build_view is not None
    wait_until(
        lambda: provider.state.get("build") is not None,
        "the automatic completed-draft build",
    )
    assert provider.state["draft"]["completed"] is True
    assert provider.state["pool"]["total_cards"] == 42
    assert build_view.property("hasBuild") is True
    build_identity = build_view.property("buildIdentity")
    assert build_identity

    for image_phase in ("loading", "ready"):
        unrelated_state = dict(provider.state)
        unrelated_state["status"] = {
            "phase": "draft_complete",
            "message": "Draft complete; image " + image_phase + ".",
        }
        unrelated_state["card_image"] = {
            "grp_id": 104983,
            "image_path": "file:///tmp/unrelated-card.jpg",
            "phase": image_phase,
            "message": "Card image " + image_phase + ".",
        }
        provider._replace_state(state=unrelated_state)
        application.processEvents()
        assert build_view.property("hasBuild") is True
        assert build_view.property("buildIdentity") == build_identity
"""
    completed = _run_qml_probe(probe, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert "TypeError" not in completed.stderr
    assert "Binding loop detected" not in completed.stderr
    assert "Unable to assign" not in completed.stderr


def test_completed_draft_build_error_clear_recovers_once() -> None:
    probe = """
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from draftomen.mock_session import MockLiveSession
from draftomen.qt_adapter import GuiPreferencesAdapter
from draftomen.qt_mock import MockSessionAdapter
from draftomen.session import ApplicationPhase, LiveSessionCommand, RequestBuild


class RecordingMockSession(MockLiveSession):
    def __init__(self, *, scenario):
        super().__init__(scenario=scenario)
        self.commands = []

    def dispatch(self, *, command: LiveSessionCommand):
        self.commands.append(command)
        return super().dispatch(command=command)


def wait_until(predicate, description):
    deadline = time.monotonic() + 5
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for " + description)
        time.sleep(0.005)
    application.processEvents()


QQuickStyle.setStyle("Fusion")
application = QGuiApplication([])
session = RecordingMockSession(scenario="build_error")
session._snapshot = replace(
    session.snapshot,
    status=replace(
        session.snapshot.status,
        phase=ApplicationPhase.DRAFT_COMPLETE,
    ),
    draft=replace(session.snapshot.draft, completed=True),
    pool=replace(session.snapshot.pool, total_cards=42),
)
provider = MockSessionAdapter(session=session)
with TemporaryDirectory() as preferences_dir:
    preferences = GuiPreferencesAdapter(
        app_dir=preferences_dir,
        parent=application,
    )
    engine = QQmlApplicationEngine()
    qml_directory = Path.cwd() / "draftomen" / "qml"
    engine.addImportPath(str(qml_directory))
    context = engine.rootContext()
    context.setContextProperty("fixedFontFamily", "monospace")
    context.setContextProperty("sessionProvider", provider)
    context.setContextProperty("applicationTitle", "Draft Omen")
    context.setContextProperty("applicationVersion", "0.0")
    context.setContextProperty("guiPreferences", preferences)
    context.setContextProperty("initialSurface", "build")
    context.setContextProperty("initialWindowWidth", 1440)
    context.setContextProperty("initialWindowHeight", 900)
    engine.setInitialProperties({"provider": provider})
    engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
    root = engine.rootObjects()[0]
    application.processEvents()
    assert provider.state["errors"][0]["operation"] == "build"
    assert root.property("automaticBuildContext") == ""
    assert session.commands == []
    unrelated_error_state = dict(provider.state)
    unrelated_error_state["status"] = dict(unrelated_error_state["status"])
    unrelated_error_state["status"]["message"] = "Build error remains visible."
    provider._replace_state(state=unrelated_error_state)
    application.processEvents()
    assert provider.state["errors"][0]["operation"] == "build"
    assert session.commands == []

    build_progress_state = dict(provider.state)
    build_progress_state["errors"] = []
    build_progress_state["build"] = None
    build_progress_state["progress"] = {
        "operation": "build",
        "message": "Building deck",
    }
    provider._replace_state(state=build_progress_state)
    application.processEvents()
    assert provider.state["progress"]["operation"] == "build"
    assert session.commands == []

    completed_state = dict(provider.state)
    completed_state["progress"] = None
    provider._replace_state(state=completed_state)
    wait_until(
        lambda: sum(isinstance(command, RequestBuild) for command in session.commands)
        == 1,
        "the deferred build request",
    )
    build_commands = [
        command for command in session.commands if isinstance(command, RequestBuild)
    ]
    assert len(build_commands) == 1
    assert root.property("automaticBuildContext") == "mock-account:mock-otj-draft"
    unrelated_state = dict(provider.state)
    unrelated_state["status"] = dict(unrelated_state["status"])
    unrelated_state["status"]["message"] = "Build remains available."
    provider._replace_state(state=unrelated_state)
    application.processEvents()
    assert sum(isinstance(command, RequestBuild) for command in session.commands) == 1
    del root
    del engine
"""
    completed = _run_qml_probe(probe)
    assert completed.returncode == 0, completed.stderr
    assert "TypeError" not in completed.stderr
    assert "Binding loop detected" not in completed.stderr
    assert "Unable to assign" not in completed.stderr


def test_qml_keyboard_controls_dispatch_account_and_ratings_commands_offscreen() -> None:
    probe = """
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtQuickControls2 import QQuickStyle

from draftomen import __version__
from draftomen.mock_session import MockLiveSession
from draftomen.qt_adapter import GuiPreferencesAdapter
from draftomen.qt_gui import _fixed_font_family
from draftomen.qt_mock import MockSessionAdapter
from draftomen.session import ChooseAccount, RequestRatingsDownload


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
preference_dir = TemporaryDirectory()
preferences = GuiPreferencesAdapter(app_dir=preference_dir.name)
engine = QQmlApplicationEngine()
qml_directory = Path.cwd() / "draftomen" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("fixedFontFamily", _fixed_font_family())
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draft Omen")
context.setContextProperty("applicationVersion", __version__)
context.setContextProperty("guiPreferences", preferences)
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
    completed = _run_qml_probe(probe)

    assert completed.returncode == 0, completed.stderr


def test_qml_tab_and_shift_tab_traversal_stays_on_surfaces_and_dialogs_offscreen() -> None:
    probe = """
from pathlib import Path
import time
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QAccessible, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from draftomen import __version__
from draftomen.mock_session import MockLiveSession
from draftomen.qt_adapter import GuiPreferencesAdapter
from draftomen.qt_gui import _fixed_font_family
from draftomen.qt_mock import MockSessionAdapter


def find_visual_item(item: QQuickItem, object_name: str) -> QQuickItem | None:
    if item.objectName() == object_name:
        return item
    for child in item.childItems():
        found = find_visual_item(child, object_name)
        if found is not None:
            return found
    return None

def assert_visual_item_inside(parent: QQuickItem, child: QQuickItem) -> None:
    parent_top_left = parent.mapToScene(QPointF(0, 0))
    parent_bottom_right = parent.mapToScene(
        QPointF(parent.width(), parent.height())
    )
    child_top_left = child.mapToScene(QPointF(0, 0))
    child_bottom_right = child.mapToScene(
        QPointF(child.width(), child.height())
    )
    assert child_top_left.x() >= parent_top_left.x()
    assert child_top_left.y() >= parent_top_left.y()
    assert child_bottom_right.x() <= parent_bottom_right.x()
    assert child_bottom_right.y() <= parent_bottom_right.y()

def wait_until(predicate, description: str) -> None:
    deadline = time.monotonic() + 5
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for " + description)
        time.sleep(0.005)
    application.processEvents()


def image_source(image: QObject) -> str:
    source = image.property("source")
    return source.toString() if isinstance(source, QUrl) else str(source)


def visible_texts(item: QQuickItem) -> list[str]:
    values = []
    text = item.property("text")
    if item.isVisible() and isinstance(text, str) and text:
        values.append(text)
    for child in item.childItems():
        values.extend(visible_texts(child))
    return values


def has_focus(item: QObject) -> bool:
    return bool(item.property("activeFocus"))


def tab_to(root: QObject, item: QObject, *, maximum: int = 40) -> None:
    for _ in range(maximum):
        QTest.keyClick(root, Qt.Key_Tab)
        application.processEvents()
        if has_focus(item):
            return
    raise AssertionError("keyboard traversal did not reach " + item.objectName())


def assert_surface_round_trip(root: QObject, item: QObject) -> None:
    tab_to(root, item)
    QTest.keyClick(root, Qt.Key_Tab)
    application.processEvents()
    assert not has_focus(item)
    QTest.keyClick(root, Qt.Key_Tab, Qt.ShiftModifier)
    application.processEvents()
    assert has_focus(item)


def assert_modal_cycle(
    root: QObject,
    opener: QObject,
    dialog_name: str,
    cancel_name: str,
    confirm_name: str,
) -> None:
    tab_to(root, opener)
    QTest.keyClick(root, Qt.Key_Space)
    application.processEvents()
    dialog = root.findChild(QObject, dialog_name)
    cancel = root.findChild(QObject, cancel_name)
    confirm = root.findChild(QObject, confirm_name)
    assert dialog is not None and dialog.property("visible") is True
    assert cancel is not None and confirm is not None
    tab_to(root, cancel, maximum=4)
    QTest.keyClick(root, Qt.Key_Tab)
    application.processEvents()
    assert has_focus(confirm)
    QTest.keyClick(root, Qt.Key_Tab)
    application.processEvents()
    assert has_focus(cancel)
    QTest.keyClick(root, Qt.Key_Tab, Qt.ShiftModifier)
    application.processEvents()
    assert has_focus(confirm)
    QTest.keyClick(root, Qt.Key_Escape)
    application.processEvents()
    assert dialog.property("visible") is False
    assert has_focus(opener)


QQuickStyle.setStyle("Fusion")
application = QGuiApplication([])
preferences_dir = TemporaryDirectory()
provider = MockSessionAdapter(session=MockLiveSession(scenario="warning"))
preferences = GuiPreferencesAdapter(app_dir=preferences_dir.name)
engine = QQmlApplicationEngine()
qml_directory = Path.cwd() / "draftomen" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("fixedFontFamily", _fixed_font_family())
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draft Omen")
context.setContextProperty("applicationVersion", __version__)
context.setContextProperty("guiPreferences", preferences)
context.setContextProperty("initialSurface", "live")
context.setContextProperty("initialWindowWidth", 1440)
context.setContextProperty("initialWindowHeight", 900)
engine.setInitialProperties({"provider": provider})
engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
root = engine.rootObjects()[0]
application.processEvents()

ranking = root.findChild(QObject, "rankingSelector")
assert ranking is not None
assert_surface_round_trip(root, ranking)
wide_preview = find_visual_item(root.contentItem(), "wideLiveCardPreview")
wide_pool = find_visual_item(root.contentItem(), "wideLivePoolDetails")
wide_row = find_visual_item(root.contentItem(), "wideRecommendationRow1")
second_row = find_visual_item(root.contentItem(), "wideRecommendationRow2")
third_row = find_visual_item(root.contentItem(), "wideRecommendationRow3")
assert wide_preview is not None and wide_preview.isVisible()
assert wide_pool is not None and wide_pool.isVisible()
assert wide_row is not None and wide_row.isVisible()
assert second_row is not None and second_row.isVisible()
assert third_row is not None and third_row.isVisible()

second_row.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Return)
application.processEvents()
assert provider.state["recommendations"]["selected_grp_id"] == (
    provider.state["recommendations"]["cards"][1]["card"]["grp_id"]
)
third_row.forceActiveFocus()
application.processEvents()
assert wide_row.property("stateText") == "Recommended"
assert second_row.property("stateText") == "Selected"
assert third_row.property("stateText") == "Keyboard focused"

image_path = Path.cwd() / "draftomen" / "assets" / "draftomen_logo.png"
image_url = QUrl.fromLocalFile(str(image_path)).toString()
missing_image_url = QUrl.fromLocalFile(
    str(Path(preferences_dir.name) / "missing-card-image.png")
).toString()
long_name = "A very long recommendation name that must remain readable within its row"
recommendations_state = dict(provider.state["recommendations"])
recommendation_cards = []
for index, recommendation in enumerate(recommendations_state["cards"]):
    updated_recommendation = dict(recommendation)
    updated_card = dict(recommendation["card"])
    updated_card["image_path"] = None
    if index == 0:
        updated_card["image_path"] = image_url
        updated_card["name"] = long_name
    elif index == 2:
        updated_card["image_path"] = missing_image_url
    updated_recommendation["card"] = updated_card
    if index == 2:
        updated_recommendation["win_rate"] = None
        updated_recommendation["letter_grade"] = None
        updated_recommendation["color_fit"] = None
        updated_recommendation["average_last_seen_at"] = None
        updated_recommendation["source_label"] = None
    recommendation_cards.append(updated_recommendation)
recommendations_state["cards"] = recommendation_cards
state_with_thumbnails = dict(provider.state)
state_with_thumbnails["recommendations"] = recommendations_state
provider._replace_state(state=state_with_thumbnails)
application.processEvents()
wide_row = find_visual_item(root.contentItem(), "wideRecommendationRow1")
second_row = find_visual_item(root.contentItem(), "wideRecommendationRow2")
third_row = find_visual_item(root.contentItem(), "wideRecommendationRow3")
assert wide_row is not None and second_row is not None and third_row is not None
wide_header_rank = find_visual_item(root.contentItem(), "recommendationHeaderRank")
wide_header_card = find_visual_item(root.contentItem(), "recommendationHeaderCard")
wide_rank = find_visual_item(wide_row, "recommendationRank")
wide_card = find_visual_item(wide_row, "recommendationCardCell")
assert wide_header_rank is not None and wide_header_card is not None
assert wide_rank is not None and wide_card is not None
assert wide_rank.mapToScene(QPointF(0, 0)).x() < \
    wide_card.mapToScene(QPointF(0, 0)).x()
assert abs(
    wide_header_rank.mapToScene(QPointF(0, 0)).x()
    - wide_rank.mapToScene(QPointF(0, 0)).x()
) <= 2
assert abs(
    wide_header_card.mapToScene(QPointF(0, 0)).x()
    - wide_card.mapToScene(QPointF(0, 0)).x()
) <= 2

wide_thumbnail_frame = find_visual_item(
    wide_row, "recommendationThumbnailFrame"
)
wide_thumbnail_image = find_visual_item(
    wide_row, "recommendationThumbnailImage"
)
wide_thumbnail_fallback = find_visual_item(
    wide_row, "recommendationThumbnailFallback"
)
wide_thumbnail_label = find_visual_item(
    wide_row, "recommendationThumbnailFallbackLabel"
)
assert wide_thumbnail_frame is not None and wide_thumbnail_frame.isVisible()
assert wide_thumbnail_image is not None
assert wide_thumbnail_fallback is not None and wide_thumbnail_label is not None
assert wide_thumbnail_frame.width() == 50
assert wide_thumbnail_frame.height() > 0
assert_visual_item_inside(wide_card, wide_thumbnail_frame)
assert wide_rank.mapToScene(QPointF(0, 0)).x() < \
    wide_thumbnail_frame.mapToScene(QPointF(0, 0)).x()
wide_thumbnail_top_left = wide_thumbnail_frame.mapToItem(
    wide_row, QPointF(0, 0)
)
wide_thumbnail_bottom_right = wide_thumbnail_frame.mapToItem(
    wide_row, QPointF(wide_thumbnail_frame.width(), wide_thumbnail_frame.height())
)
assert wide_thumbnail_top_left.x() >= -1
assert wide_thumbnail_top_left.y() >= -1
assert wide_thumbnail_bottom_right.x() <= wide_row.width() + 1
assert wide_thumbnail_bottom_right.y() <= wide_row.height() + 1
wait_until(
    lambda: wide_thumbnail_image.isVisible(),
    "the visible locally published recommendation thumbnail",
)
assert image_source(wide_thumbnail_image) == image_url
assert wide_thumbnail_fallback.isVisible() is False

wide_missing_fallback = find_visual_item(
    second_row, "recommendationThumbnailFallback"
)
wide_missing_label = find_visual_item(
    second_row, "recommendationThumbnailFallbackLabel"
)
assert wide_missing_fallback is not None and wide_missing_fallback.isVisible()
assert wide_missing_label is not None
assert wide_missing_label.property("text") == "No image available"

wide_error_image = find_visual_item(third_row, "recommendationThumbnailImage")
wide_error_fallback = find_visual_item(
    third_row, "recommendationThumbnailFallback"
)
wide_error_label = find_visual_item(
    third_row, "recommendationThumbnailFallbackLabel"
)
assert wide_error_image is not None
assert wide_error_fallback is not None and wide_error_label is not None
wait_until(
    lambda: wide_error_fallback.isVisible()
    and wide_error_label.property("text") == "Image failed to load",
    "the labelled failed recommendation thumbnail fallback",
)
assert wide_error_image.isVisible() is False
assert wide_preview.mapToItem(root.contentItem(), QPointF(0, wide_preview.height())).y() \
    <= wide_pool.mapToItem(root.contentItem(), QPointF(0, 0)).y()
assert wide_preview.property("imageFrameHeight") > 0
assert wide_preview.findChild(QObject, "cardPreviewFacts") is not None
assert wide_preview.findChild(QObject, "cardPreviewScores") is not None
assert wide_preview.findChild(QObject, "cardPreviewExplanation") is not None
wide_frame = find_visual_item(wide_preview, "cardPreviewImageFrame")
wide_details = find_visual_item(wide_preview, "cardPreviewDetails")
assert wide_frame is not None and wide_frame.isVisible()
assert wide_details is not None and wide_details.isVisible()
assert 440 <= wide_preview.width() <= 450
assert wide_row.width() > wide_preview.width()
assert wide_frame.width() >= 195, (
    wide_preview.width(),
    wide_preview.height(),
    wide_frame.width(),
    wide_frame.height(),
    wide_details.width(),
    wide_details.height(),
)
assert wide_details.width() < wide_preview.width() / 2, (
    wide_preview.width(),
    wide_preview.height(),
    wide_frame.width(),
    wide_frame.height(),
    wide_details.width(),
    wide_details.height(),
)
assert_visual_item_inside(wide_preview, wide_frame)
assert_visual_item_inside(wide_preview, wide_details)
confidence = root.findChild(QObject, "recommendationConfidenceSummary")
assert confidence is not None
state_without_confidence = dict(provider.state)
recommendations_without_confidence = dict(state_without_confidence["recommendations"])
recommendations_without_confidence["confidence_summary"] = None
state_without_confidence["recommendations"] = recommendations_without_confidence
provider._replace_state(state=state_without_confidence)
application.processEvents()
assert confidence.isVisible() is False
state_with_confidence = dict(provider.state)
recommendations_with_confidence = dict(state_with_confidence["recommendations"])
recommendations_with_confidence["confidence_summary"] = "Current pool confidence: 64%"
state_with_confidence["recommendations"] = recommendations_with_confidence
provider._replace_state(state=state_with_confidence)
application.processEvents()
assert confidence.isVisible()
assert confidence.property("text") == "Current pool confidence: 64%"
wide_row = find_visual_item(root.contentItem(), "wideRecommendationRow1")
second_row = find_visual_item(root.contentItem(), "wideRecommendationRow2")
third_row = find_visual_item(root.contentItem(), "wideRecommendationRow3")
assert wide_row is not None and second_row is not None and third_row is not None
assert wide_pool.findChild(QObject, "poolCount") is not None
pool_count = wide_pool.findChild(QObject, "poolCount")
assert pool_count is not None
pool_state = provider.state["pool"]
assert pool_count.property("text") == (
    f'{pool_state["total_cards"]} / {pool_state["target_cards"]} cards'
)
pool_average = wide_pool.findChild(QObject, "poolManaCurveAverage")
assert pool_average is not None
assert pool_average.property("text") == "Average mana value: 2.43"
unavailable_pool_state = dict(provider.state)
unavailable_pool = dict(unavailable_pool_state["pool"])
unavailable_pool["average_mana_value"] = None
unavailable_pool_state["pool"] = unavailable_pool
provider._replace_state(state=unavailable_pool_state)
application.processEvents()
assert pool_average.property("text") == "Average mana value: —"
provider.selectScenario("warning")
application.processEvents()
restored_state = dict(provider.state)
restored_state["recommendations"] = recommendations_state
provider._replace_state(state=restored_state)
application.processEvents()
wide_row = find_visual_item(root.contentItem(), "wideRecommendationRow1")
second_row = find_visual_item(root.contentItem(), "wideRecommendationRow2")
third_row = find_visual_item(root.contentItem(), "wideRecommendationRow3")
assert wide_row is not None and second_row is not None and third_row is not None
pool_flickable = wide_pool.findChild(QObject, "poolSummaryFlickable")
pool_scrollbar = wide_pool.findChild(QObject, "poolSummaryScrollBar")
assert pool_flickable is not None and pool_flickable.property("activeFocusOnTab") is True
assert pool_scrollbar is not None and pool_scrollbar.isVisible()
accessible_pool = QAccessible.queryAccessibleInterface(pool_flickable)
assert accessible_pool is not None
assert "Page Up" in accessible_pool.text(QAccessible.Text.Description)
pool_flickable.forceActiveFocus()
assert pool_flickable.property("activeFocus") is True
max_pool_scroll = max(
    0.0,
    float(pool_flickable.property("contentHeight"))
    - float(pool_flickable.property("height")),
)
assert max_pool_scroll > 0
QTest.keyClick(root, Qt.Key_Down)
application.processEvents()
assert pool_flickable.property("contentY") > 0
QTest.keyClick(root, Qt.Key_Up)
application.processEvents()
assert pool_flickable.property("contentY") == 0
QTest.keyClick(root, Qt.Key_PageDown)
application.processEvents()
assert pool_flickable.property("contentY") > 0
QTest.keyClick(root, Qt.Key_PageUp)
application.processEvents()
assert pool_flickable.property("contentY") == 0
QTest.keyClick(root, Qt.Key_End)
application.processEvents()
assert abs(float(pool_flickable.property("contentY")) - max_pool_scroll) < 1
QTest.keyClick(root, Qt.Key_Home)
application.processEvents()
assert pool_flickable.property("contentY") == 0
name = wide_row.findChild(QObject, "recommendationName")
assert name is not None
assert name.property("text") == provider.state["recommendations"]["cards"][0]["card"]["name"]
assert name.property("truncated") is False
assert name.property("paintedWidth") <= name.property("width") + 1
assert name.property("paintedHeight") <= name.property("height") + 1
assert_visual_item_inside(wide_card, name)
metadata = wide_row.findChild(QObject, "recommendationMetadata")
assert metadata is not None
assert_visual_item_inside(wide_card, metadata)
first_card = provider.state["recommendations"]["cards"][0]
wide_metrics = {
    "recommendationColors": " · ".join(first_card["card"]["colors"]) or "Colorless",
    "recommendationScore": str(first_card["score"]),
    "recommendationWinRate": f'{first_card["win_rate"] * 100:.1f}%',
    "recommendationGrade": first_card["letter_grade"],
    "recommendationFit": first_card["color_fit"],
}
for metric_name, metric_text in wide_metrics.items():
    metric = wide_row.findChild(QObject, metric_name)
    assert metric is not None and metric.isVisible()
    assert metric.property("text") == metric_text
    assert_visual_item_inside(wide_row, metric)
assert name.property("text") == long_name
assert wide_row.height() == 84
assert wide_row.width() <= wide_row.parentItem().width() + 1
live_view = root.findChild(QObject, "liveDraftView")
assert live_view is not None
wide_content_threshold = int(
    live_view.property("wideRecommendationsMinimumWidth")
)
window_threshold_width = root.width() + wide_content_threshold - live_view.width()
root.resize(window_threshold_width, 900)
application.processEvents()
assert live_view.property("wideRecommendations") is True
assert find_visual_item(root.contentItem(), "wideRecommendationRow1").isVisible()
root.resize(window_threshold_width - 1, 900)
application.processEvents()
assert live_view.property("wideRecommendations") is False
boundary_narrow_row = find_visual_item(
    root.contentItem(), "narrowRecommendationRow1"
)
assert boundary_narrow_row is not None and boundary_narrow_row.isVisible()
assert boundary_narrow_row.height() == 112
assert boundary_narrow_row.property("recommendation")["card"]["name"] == long_name
boundary_narrow_details = find_visual_item(
    boundary_narrow_row, "recommendationNarrowCardDetails"
)
assert boundary_narrow_details is not None and boundary_narrow_details.isVisible()
boundary_narrow_name = boundary_narrow_details.findChild(
    QObject, "recommendationName"
)
assert boundary_narrow_name is not None
assert boundary_narrow_name.property("text") == long_name
root.resize(1440, 900)
application.processEvents()
assert live_view.property("wideRecommendations") is True


root.resize(760, 900)
application.processEvents()
narrow_row = find_visual_item(root.contentItem(), "narrowRecommendationRow1")
narrow_second_row = find_visual_item(root.contentItem(), "narrowRecommendationRow2")
narrow_third_row = find_visual_item(root.contentItem(), "narrowRecommendationRow3")
assert narrow_row is not None and narrow_row.isVisible()
assert narrow_second_row is not None and narrow_second_row.isVisible()
assert narrow_third_row is not None
narrow_thumbnail_frame = find_visual_item(
    narrow_row, "recommendationThumbnailFrame"
)
narrow_thumbnail_image = find_visual_item(
    narrow_row, "recommendationThumbnailImage"
)
narrow_thumbnail_fallback = find_visual_item(
    narrow_row, "recommendationThumbnailFallback"
)
assert narrow_thumbnail_frame is not None and narrow_thumbnail_frame.isVisible()
assert narrow_thumbnail_image is not None and narrow_thumbnail_fallback is not None
assert narrow_thumbnail_frame.width() == 50
assert narrow_thumbnail_frame.height() > 0
assert narrow_row.height() == 112
assert narrow_row.width() <= narrow_row.parentItem().width() + 1
narrow_thumbnail_top_left = narrow_thumbnail_frame.mapToItem(
    narrow_row, QPointF(0, 0)
)
narrow_thumbnail_bottom_right = narrow_thumbnail_frame.mapToItem(
    narrow_row,
    QPointF(narrow_thumbnail_frame.width(), narrow_thumbnail_frame.height()),
)
assert narrow_thumbnail_top_left.x() >= -1
assert narrow_thumbnail_top_left.y() >= -1
assert narrow_thumbnail_bottom_right.x() <= narrow_row.width() + 1
assert narrow_thumbnail_bottom_right.y() <= narrow_row.height() + 1
wait_until(
    lambda: narrow_thumbnail_image.isVisible(),
    "the visible narrow recommendation thumbnail",
)
assert image_source(narrow_thumbnail_image) == image_url
assert narrow_thumbnail_fallback.isVisible() is False

narrow_missing_fallback = find_visual_item(
    narrow_second_row, "recommendationThumbnailFallback"
)
narrow_missing_label = find_visual_item(
    narrow_second_row, "recommendationThumbnailFallbackLabel"
)
assert narrow_missing_fallback is not None and narrow_missing_fallback.isVisible()
assert narrow_missing_label is not None
assert narrow_missing_label.property("text") == "No image available"
narrow_third_texts = visible_texts(narrow_third_row)
assert "17L —" in narrow_third_texts
assert "Grade —" in narrow_third_texts
assert "Open" in narrow_third_texts
root.resize(1440, 900)
application.processEvents()
provider.selectScenario("empty")
application.processEvents()
assert confidence.isVisible() is False
provider.selectScenario("ready")
application.processEvents()
assert confidence.isVisible() is False
assert len(provider.state["pool"]["mana_curve"]) == 7
assert pool_state["color_distribution"]
provider.selectScenario("warning")
application.processEvents()
download = root.findChild(QObject, "ratingsDownloadButton")
assert download is not None
assert_modal_cycle(
    root,
    download,
    "ratingsDownloadDialog",
    "ratingsDownloadCancelButton",
    "ratingsDownloadConfirmButton",
)

root.setProperty("currentSurface", "build")
application.processEvents()
pair_selector = root.findChild(QObject, "buildPairSelector")
assert pair_selector is not None
assert_surface_round_trip(root, pair_selector)

root.setProperty("currentSurface", "backtest")
application.processEvents()
run_backtest = root.findChild(QObject, "backtestRunButton")
assert run_backtest is not None
assert_surface_round_trip(root, run_backtest)

root.setProperty("currentSurface", "settings")
application.processEvents()
preview_switch = root.findChild(QObject, "settingsCardPreviewSwitch")
assert preview_switch is not None
assert_surface_round_trip(root, preview_switch)
settings_download = root.findChild(QObject, "settingsRatingsDownloadButton")
assert settings_download is not None
assert_modal_cycle(
    root,
    settings_download,
    "settingsRatingsDownloadDialog",
    "settingsRatingsDownloadCancelButton",
    "settingsRatingsDownloadConfirmButton",
)
"""
    completed = _run_qml_probe(probe)

    assert completed.returncode == 0, completed.stderr


def test_qml_preferences_build_backtest_and_responsive_states_offscreen() -> None:
    probe = """
from tempfile import TemporaryDirectory
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QAccessible, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle

from draftomen import __version__
from draftomen.mock_session import MockLiveSession
from draftomen.qt_adapter import GuiPreferencesAdapter
from draftomen.qt_gui import _fixed_font_family
from draftomen.qt_mock import MockSessionAdapter
from draftomen.session import FocusBuildCard, RequestBacktest, RequestBuild

def find_visual_item(item: QQuickItem, object_name: str) -> QQuickItem | None:
    if item.objectName() == object_name:
        return item
    for child in item.childItems():
        found = find_visual_item(child, object_name)
        if found is not None:
            return found
    return None


def assert_visible_active_focus(item: QQuickItem) -> None:
    assert item.property("activeFocus") is True
    assert item.isVisible()
    top_left = item.mapToScene(QPointF(0, 0))
    bottom_right = item.mapToScene(QPointF(item.width(), item.height()))
    assert top_left.y() >= 0
    assert bottom_right.y() <= root.height()


def assert_visual_item_inside(parent: QQuickItem, child: QQuickItem) -> None:
    parent_top_left = parent.mapToScene(QPointF(0, 0))
    parent_bottom_right = parent.mapToScene(QPointF(parent.width(), parent.height()))
    child_top_left = child.mapToScene(QPointF(0, 0))
    child_bottom_right = child.mapToScene(QPointF(child.width(), child.height()))
    assert child_top_left.x() >= parent_top_left.x()
    assert child_top_left.y() >= parent_top_left.y()
    assert child_bottom_right.x() <= parent_bottom_right.x()
    assert child_bottom_right.y() <= parent_bottom_right.y()


def assert_visual_item_precedes(first: QQuickItem, second: QQuickItem) -> None:
    first_bottom = first.mapToScene(QPointF(0, first.height())).y()
    second_top = second.mapToScene(QPointF(0, 0)).y()
    assert first_bottom <= second_top


def trigger_and_wait_for_layout(item, action, predicate) -> None:
    height_change_spy = QSignalSpy(item.heightChanged)
    action()
    while not predicate(item.height()):
        assert height_change_spy.wait(1000)
    application.processEvents()


class RecordingProvider(MockSessionAdapter):
    def __init__(self):
        self.commands = []
        super().__init__(session=MockLiveSession(scenario="ready"))

    def _dispatch(self, *, command):
        self.commands.append(command)
        super()._dispatch(command=command)


preferences_dir = TemporaryDirectory()
QQuickStyle.setStyle("Fusion")
application = QGuiApplication([])
provider = RecordingProvider()
preferences = GuiPreferencesAdapter(app_dir=preferences_dir.name)
preferences.setCardPreview(False)
engine = QQmlApplicationEngine()
qml_directory = Path.cwd() / "draftomen" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("fixedFontFamily", _fixed_font_family())
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("guiPreferences", preferences)
context.setContextProperty("applicationTitle", "Draft Omen")
context.setContextProperty("applicationVersion", __version__)
context.setContextProperty("initialSurface", "settings")
context.setContextProperty("initialWindowWidth", 1440)
context.setContextProperty("initialWindowHeight", 900)
engine.setInitialProperties({"provider": provider})
engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
root = engine.rootObjects()[0]
application.processEvents()
assert root.property("desktopApplicationVersion") == __version__
version_label = root.findChild(QObject, "applicationVersionLabel")
assert version_label is not None and version_label.property("text") == "v" + __version__
logo = find_visual_item(root.contentItem(), "draftomenLogo")
app_bar_title = root.findChild(QObject, "appBarBrandTitle")
assert logo is not None and logo.isVisible()
assert app_bar_title is not None and app_bar_title.isVisible() is False
accessible_logo = QAccessible.queryAccessibleInterface(logo)
assert accessible_logo is not None
assert accessible_logo.text(QAccessible.Text.Name) == "Draft Omen logo"

preview_switch = root.findChild(QObject, "settingsCardPreviewSwitch")
assert preview_switch is not None and preview_switch.property("checked") is False
preview_switch.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert preferences.cardPreview is True
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert preferences.cardPreview is False
assert GuiPreferencesAdapter(app_dir=preferences_dir.name).cardPreview is False

root.resize(1080, 900)
root.setProperty("currentSurface", "live")
application.processEvents()
responsive_live_view = root.findChild(QObject, "liveDraftView")
assert responsive_live_view is not None
assert responsive_live_view.property("wideRecommendations") is False
responsive_narrow_row = find_visual_item(
    root.contentItem(), "narrowRecommendationRow1"
)
responsive_narrow_preview = find_visual_item(
    root.contentItem(), "narrowLiveCardPreview"
)
assert responsive_narrow_row is not None and responsive_narrow_row.isVisible()
assert responsive_narrow_row.height() == 112
assert responsive_narrow_preview is not None and responsive_narrow_preview.isVisible()
assert responsive_narrow_row.property("recommendation")["card"]["name"] == (
    provider.state["recommendations"]["cards"][0]["card"]["name"]
)

root.resize(760, 900)
root.setProperty("currentSurface", "live")
application.processEvents()
live_tabs = root.findChild(QObject, "liveDetailTabs")
narrow_live_preview = find_visual_item(root.contentItem(), "narrowLiveCardPreview")
narrow_live_pool = find_visual_item(root.contentItem(), "narrowLivePoolDetails")
narrow_row = find_visual_item(root.contentItem(), "narrowRecommendationRow1")
assert live_tabs is not None and live_tabs.property("currentIndex") == 0
assert narrow_live_preview is not None and narrow_live_preview.isVisible()
assert narrow_live_pool is not None and narrow_live_pool.isVisible() is False
assert narrow_row is not None and narrow_row.height() >= 100
assert narrow_row.width() == live_tabs.width()
assert narrow_live_preview.width() == live_tabs.width()
narrow_frame = find_visual_item(narrow_live_preview, "cardPreviewImageFrame")
assert narrow_frame is not None and narrow_frame.isVisible()
assert narrow_frame.width() >= 195
assert_visual_item_inside(narrow_live_preview, narrow_frame)
assert narrow_row.width() > root.width() / 2
assert logo.isVisible() is False
assert app_bar_title.isVisible()

live_tabs.setProperty("currentIndex", 1)
application.processEvents()
assert narrow_live_preview.isVisible() is False
assert narrow_live_pool.isVisible()

live_tabs.setProperty("currentIndex", 0)
application.processEvents()
assert narrow_live_preview.isVisible()
preferences.setCardPreview(True)
application.processEvents()

root.setProperty("currentSurface", "build")
application.processEvents()
build_view = root.findChild(QObject, "buildView")
narrow_type_summary = find_visual_item(root.contentItem(), "narrowBuildTypeSummary")
assert narrow_type_summary is not None and narrow_type_summary.isVisible()
assert narrow_type_summary.property("text") == "7 creatures · 1 instant"
changed_state = dict(provider.state)
changed_build = dict(changed_state["build"])
changed_build["creature_count"] = 5
changed_build["instant_count"] = 2
changed_state["build"] = changed_build
provider._replace_state(state=changed_state)
application.processEvents()
assert narrow_type_summary.property("text") == "5 creatures · 2 instants"
provider.selectScenario("ready")
application.processEvents()
assert narrow_type_summary.property("text") == "7 creatures · 1 instant"
assert build_view is not None
initial_focus_key = build_view.property("publishedBuildFocusKey")
assert initial_focus_key
focus_command_count = sum(
    isinstance(command, FocusBuildCard) for command in provider.commands
)
provider.selectScenario("empty")
application.processEvents()
assert build_view.property("publishedBuildFocusKey") == ""
provider.selectScenario("ready")
application.processEvents()
assert build_view.property("publishedBuildFocusKey") == initial_focus_key
assert sum(
    isinstance(command, FocusBuildCard) for command in provider.commands
) == focus_command_count + 1

narrow_curve = find_visual_item(root.contentItem(), "narrowBuildManaCurve")
assert narrow_curve is not None and narrow_curve.isVisible()
narrow_average = find_visual_item(narrow_curve, "manaCurveAverage")
assert narrow_average is not None and narrow_average.isVisible()
assert narrow_average.width() >= narrow_average.property("implicitWidth")
formatted_state = dict(provider.state)
formatted_build = dict(formatted_state["build"])
formatted_build["average_mana_value"] = 2.956
formatted_state["build"] = formatted_build
provider._replace_state(state=formatted_state)
application.processEvents()
assert narrow_average.property("text") == "Average mana value: 2.96"
unavailable_state = dict(provider.state)
unavailable_build = dict(unavailable_state["build"])
unavailable_build["average_mana_value"] = None
unavailable_state["build"] = unavailable_build
provider._replace_state(state=unavailable_state)
application.processEvents()
assert narrow_average.property("text") == "Average mana value: —"
provider.selectScenario("ready")
application.processEvents()
assert narrow_average.property("text") == "Average mana value: 2.70"

pair_selector = root.findChild(QObject, "buildPairSelector")
rebuild = root.findChild(QObject, "buildRebuildButton")
assert pair_selector is not None and rebuild is not None
pair_selector.setProperty("currentIndex", 1)
rebuild.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert any(isinstance(command, RequestBuild) and command.pair_override == "BG" for command in provider.commands)
assert provider.state["build"]["selected_pair"] == "BG"
assert narrow_type_summary.property("text") == "7 creatures · 1 instant"
spell_button = find_visual_item(root.contentItem(), "buildSpellButton1")
assert build_view is not None and spell_button is not None
spell_button.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
QTest.qWait(20)
card_details_toggle = find_visual_item(root.contentItem(), "buildCardDetailsToggle")
assert card_details_toggle is not None
assert_visible_active_focus(card_details_toggle)
assert build_view.property("focusedCard")["card"]["name"] == provider.state["build"]["spells"][1]["card"]["name"]
assert spell_button.property("selected") is True
accessible_spell = QAccessible.queryAccessibleInterface(spell_button)
assert accessible_spell is not None
assert accessible_spell.object() is spell_button
assert spell_button.property("accessibilitySelectable") is True
assert spell_button.property("accessibilitySelected") is True
narrow_preview = find_visual_item(root.contentItem(), "narrowBuildCardPreview")
assert narrow_preview is not None and narrow_preview.isVisible()
assert narrow_preview.property("recommendation")["card"]["name"] == provider.state["build"]["spells"][1]["card"]["name"]
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert_visible_active_focus(card_details_toggle)
bench_toggle = root.findChild(QObject, "buildBenchToggle")
assert bench_toggle is not None
assert bench_toggle.property("text").endswith("bench · 2")
bench_toggle.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
bench_button = find_visual_item(root.contentItem(), "buildBenchButton0")
assert bench_button is not None
bench_button.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert narrow_preview.property("recommendation")["card"]["name"] == provider.state["build"]["bench"][0]["card"]["name"]
assert any(
    isinstance(command, FocusBuildCard)
    and command.grp_id == provider.state["build"]["bench"][0]["card"]["grp_id"]
    for command in provider.commands
)
assert narrow_preview.property("imageState")["grp_id"] == provider.state["build"]["bench"][0]["card"]["grp_id"]
QTest.qWait(20)
assert_visible_active_focus(card_details_toggle)

build_view.setProperty("contextExpanded", False)
root.resize(1059, 900)
application.processEvents()
assert root.property("narrow") is False
assert build_view.property("compactPresentation") is True
assert build_view.property("contextExpanded") is False
assert pair_selector.isVisible()
assert rebuild.isVisible()
compact_spell = find_visual_item(root.contentItem(), "buildSpellButton1")
assert compact_spell is not None and compact_spell.isVisible()
narrow_context = find_visual_item(root.contentItem(), "narrowBuildContext")
narrow_context_toggle = find_visual_item(root.contentItem(), "narrowBuildContextToggle")
narrow_context_details = find_visual_item(root.contentItem(), "narrowBuildContextDetails")
assert narrow_context is not None and narrow_context.isVisible()
assert narrow_context_toggle is not None and narrow_context_toggle.isVisible()
assert narrow_context_details is not None and narrow_context_details.isVisible() is False
assert narrow_context_toggle.property("text") == "Show why this pair"
collapsed_narrow_context_height = narrow_context.height()
accessible_narrow_context = QAccessible.queryAccessibleInterface(narrow_context_toggle)
assert accessible_narrow_context is not None
assert accessible_narrow_context.text(QAccessible.Text.Name) == "Show why this pair"
assert accessible_narrow_context.text(QAccessible.Text.Description) == (
    "The pair rationale is currently collapsed. Activating this button expands it."
)
narrow_context_toggle.forceActiveFocus()
trigger_and_wait_for_layout(
    narrow_context,
    lambda: QTest.keyClick(root, Qt.Key_Space),
    lambda height: height > collapsed_narrow_context_height,
)
assert build_view.property("contextExpanded") is True
assert narrow_context_toggle.property("text") == "Hide why this pair"
assert accessible_narrow_context.text(QAccessible.Text.Name) == "Hide why this pair"
assert accessible_narrow_context.text(QAccessible.Text.Description) == (
    "The pair rationale is currently expanded. Activating this button collapses it."
)
assert narrow_context_details.isVisible()
narrow_reason = find_visual_item(root.contentItem(), "narrowPairOptionWG")
assert narrow_reason is not None and narrow_reason.isVisible()
assert narrow_context.height() > collapsed_narrow_context_height
trigger_and_wait_for_layout(
    narrow_context,
    lambda: QTest.keyClick(root, Qt.Key_Space),
    lambda height: height == collapsed_narrow_context_height,
)
assert build_view.property("contextExpanded") is False
assert narrow_context_toggle.property("text") == "Show why this pair"
assert narrow_context_details.isVisible() is False
assert narrow_reason.isVisible() is False
assert narrow_context.height() == collapsed_narrow_context_height

root.resize(1060, 900)
application.processEvents()
assert build_view.property("compactPresentation") is False
wide_creature_count = find_visual_item(root.contentItem(), "wideBuildCreatureCount")
wide_instant_count = find_visual_item(root.contentItem(), "wideBuildInstantCount")
assert wide_creature_count is not None and wide_creature_count.isVisible()
assert wide_instant_count is not None and wide_instant_count.isVisible()
assert wide_creature_count.property("text") == "7"
assert wide_instant_count.property("text") == "1"
assert pair_selector.isVisible()
assert rebuild.isVisible()

wide_curve = find_visual_item(root.contentItem(), "wideBuildManaCurve")
assert wide_curve is not None and wide_curve.isVisible()
wide_mana_base = find_visual_item(root.contentItem(), "wideBuildManaBase")
wide_warnings = find_visual_item(root.contentItem(), "wideBuildWarnings")
assert wide_mana_base is not None and wide_mana_base.isVisible()
assert wide_warnings is not None and wide_warnings.isVisible()
assert_visual_item_precedes(wide_mana_base, wide_curve)
assert_visual_item_precedes(wide_curve, wide_warnings)
wide_title = find_visual_item(wide_curve, "manaCurveTitle")
assert wide_title is not None and wide_title.isVisible()
assert wide_title.width() <= wide_curve.width()
assert wide_title.width() >= wide_title.property("implicitWidth")
assert_visual_item_inside(wide_curve, wide_title)
wide_average = find_visual_item(wide_curve, "manaCurveAverage")
assert wide_average is not None and wide_average.isVisible()
assert wide_average.property("text") == "Average mana value: 2.70"
assert wide_average.width() <= wide_curve.width()
assert wide_average.width() >= wide_average.property("implicitWidth")
assert_visual_item_inside(wide_curve, wide_average)
assert_visual_item_precedes(wide_title, wide_average)
wide_spells = find_visual_item(root.contentItem(), "buildSpellGroups")
assert wide_spells is not None and wide_spells.isVisible()
assert wide_spells.x() >= 0
assert wide_spells.x() + wide_spells.width() <= build_view.width()
wide_context = find_visual_item(root.contentItem(), "wideBuildContext")
wide_context_toggle = find_visual_item(root.contentItem(), "wideBuildContextToggle")
wide_context_details = find_visual_item(root.contentItem(), "wideBuildContextDetails")
assert wide_context is not None and wide_context.isVisible()
assert wide_context_toggle is not None and wide_context_toggle.isVisible()
assert wide_context_details is not None and wide_context_details.isVisible() is False
assert wide_context_toggle.property("text") == "Show why this pair"
collapsed_wide_context_height = wide_context.height()
accessible_wide_context = QAccessible.queryAccessibleInterface(wide_context_toggle)
assert accessible_wide_context is not None
assert accessible_wide_context.text(QAccessible.Text.Name) == "Show why this pair"
assert accessible_wide_context.text(QAccessible.Text.Description) == (
    "The pair rationale is currently collapsed. Activating this button expands it."
)
wide_context_toggle.forceActiveFocus()
trigger_and_wait_for_layout(
    wide_context,
    lambda: QTest.keyClick(root, Qt.Key_Space),
    lambda height: height > collapsed_wide_context_height,
)
assert build_view.property("contextExpanded") is True
assert wide_context_toggle.property("text") == "Hide why this pair"
assert accessible_wide_context.text(QAccessible.Text.Name) == "Hide why this pair"
assert accessible_wide_context.text(QAccessible.Text.Description) == (
    "The pair rationale is currently expanded. Activating this button collapses it."
)
assert wide_context_details.isVisible()
wide_reason = find_visual_item(root.contentItem(), "widePairOptionWG")
assert wide_reason is not None and wide_reason.isVisible()
assert "score 82.4" in wide_reason.property("text")
assert "25 playables" in wide_reason.property("text")
assert wide_context.height() > collapsed_wide_context_height
trigger_and_wait_for_layout(
    wide_context,
    lambda: QTest.keyClick(root, Qt.Key_Space),
    lambda height: height == collapsed_wide_context_height,
)
assert build_view.property("contextExpanded") is False
assert wide_context_toggle.property("text") == "Show why this pair"
assert wide_context_details.isVisible() is False
assert wide_reason.isVisible() is False
assert wide_context.height() == collapsed_wide_context_height

build_view.setProperty("benchExpanded", False)
application.processEvents()
wide_bench_toggle = find_visual_item(root.contentItem(), "buildBenchToggle")
assert wide_bench_toggle is not None and wide_bench_toggle.isVisible()
assert wide_bench_toggle.property("text").endswith("bench · 2")
wide_bench_toggle.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
wide_bench = find_visual_item(root.contentItem(), "buildBenchButton0")
assert wide_bench is not None and wide_bench.isVisible()
wide_bench.forceActiveFocus()
application.processEvents()
assert wide_bench.property("activeFocus") is True

root.resize(1440, 900)
application.processEvents()
wide_preview = find_visual_item(root.contentItem(), "wideBuildCardPreview")
wide_curve = find_visual_item(root.contentItem(), "wideBuildManaCurve")
assert wide_preview is not None and wide_preview.isVisible()
assert wide_curve is not None and wide_curve.isVisible()
assert_visual_item_inside(build_view, wide_preview)
wide_heading = find_visual_item(wide_preview, "cardPreviewHeading")
wide_frame = find_visual_item(wide_preview, "cardPreviewImageFrame")
wide_fallback = find_visual_item(wide_preview, "cardPreviewFallback")
wide_details = find_visual_item(wide_preview, "cardPreviewDetails")
assert wide_heading is not None and wide_heading.isVisible()
assert wide_frame is not None and wide_frame.isVisible()
assert abs(wide_frame.height() - wide_frame.width() * 1.4) <= 0.1
assert wide_details is not None and wide_details.isVisible()
assert wide_frame.width() > 240
assert wide_preview.width() - wide_frame.width() >= 40
assert abs(wide_details.width() - wide_frame.width()) <= 1
assert abs(wide_details.x() - wide_frame.x()) <= 1
assert wide_details.y() - (wide_frame.y() + wide_frame.height()) >= 18
assert wide_fallback is not None and wide_fallback.isVisible()
for item in (wide_heading, wide_frame, wide_fallback, wide_details):
    assert_visual_item_inside(wide_preview, item)
assert_visual_item_inside(build_view, wide_curve)
accessible_preview = QAccessible.queryAccessibleInterface(wide_preview)
assert accessible_preview is not None
assert accessible_preview.text(QAccessible.Text.Name) == (
    "Selected card, " + provider.state["build"]["bench"][0]["card"]["name"]
)


provider.selectScenario("build_error")
application.processEvents()
build_request = root.findChild(QObject, "buildRequestButton")
assert build_request is not None
assert provider.state["errors"][0]["operation"] == "build"
build_request.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert any(
    isinstance(command, RequestBuild) and command.pair_override is None
    for command in provider.commands
)

root.setProperty("currentSurface", "backtest")
application.processEvents()
backtest_view = find_visual_item(root.contentItem(), "backtestView")
assert backtest_view is not None
run_backtest = root.findChild(QObject, "backtestRunButton")
assert run_backtest is not None
run_backtest.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert any(isinstance(command, RequestBacktest) for command in provider.commands)
subtitle = root.findChild(QObject, "backtestSubtitle")
assert subtitle is not None
assert "MagoAnubiTest (mock-account)" in subtitle.property("text")
assert "Draft mock-otj-draft" in subtitle.property("text")
assert "OTJ" in subtitle.property("text")
assert "Quick Draft" in subtitle.property("text")
root.resize(1440, 900)
application.processEvents()
win_rate_header = find_visual_item(root.contentItem(), "backtestWinRateHeader")
win_rate = find_visual_item(root.contentItem(), "backtestWinRate0")
assert win_rate_header is not None and win_rate_header.isVisible()
assert win_rate is not None and win_rate.isVisible() and win_rate.property("text") == "63.6%"

root.setProperty("currentSurface", "settings")
application.processEvents()
secondary_stats = root.findChild(QObject, "settingsSecondaryStatsSwitch")
assert secondary_stats is not None and secondary_stats.property("checked") is True
secondary_stats.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert preferences.secondaryStats is False

root.setProperty("currentSurface", "backtest")
application.processEvents()
assert win_rate_header.isVisible() is False
assert win_rate.isVisible() is False
source_header = find_visual_item(root.contentItem(), "backtestSourceHeader")
source = find_visual_item(root.contentItem(), "backtestSource0")
assert source_header is not None and source_header.isVisible() is False
assert source is not None and source.isVisible() is False
for name in ("backtestRecommended0", "backtestActual0", "backtestResult0", "backtestScore0"):
    primary = find_visual_item(root.contentItem(), name)
    assert primary is not None and primary.isVisible()
root.resize(760, 900)
application.processEvents()
narrow_secondary = find_visual_item(root.contentItem(), "backtestNarrowSecondary0")
assert narrow_secondary is not None and narrow_secondary.isVisible() is False
narrow_score = find_visual_item(root.contentItem(), "backtestNarrowScore0")
assert narrow_score is not None and narrow_score.isVisible()


provider.selectScenario("backtest_missing")
application.processEvents()
rows = root.findChild(QObject, "backtestRows")
assert rows is not None
assert rows.property("count") == 1
assert provider.state["backtest"]["skipped_count"] == 1
subtitle = root.findChild(QObject, "backtestSubtitle")
assert subtitle is not None
assert "recorded picks" in subtitle.property("text")


root.resize(760, 900)
application.processEvents()
assert root.property("narrow") is True
assert root.width() == 760
assert rows.width() <= root.width()
root.resize(1440, 900)
application.processEvents()
assert root.property("narrow") is False
assert root.width() == 1440

provider.selectScenario("backtest_error")
application.processEvents()
subtitle = root.findChild(QObject, "backtestSubtitle")
assert subtitle is not None
assert subtitle.property("text") == "Compare persisted picks with the active ranking"
dismiss = find_visual_item(backtest_view, "sessionErrorDismissButton")
assert dismiss is not None and dismiss.property("visible") is True
dismiss.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert provider.state["errors"] == []

provider.selectScenario("backtest_error")
application.processEvents()
retry = find_visual_item(backtest_view, "sessionErrorRetryButton")
assert retry is not None and retry.property("visible") is True
retry.forceActiveFocus()
QTest.keyClick(root, Qt.Key_Space)
application.processEvents()
assert provider.state["errors"] == []

del engine

"""
    completed = _run_qml_probe(probe)

    assert completed.returncode == 0, completed.stderr
    assert "Binding loop detected" not in completed.stderr
    assert "Unable to assign" not in completed.stderr
    assert "TypeError" not in completed.stderr

