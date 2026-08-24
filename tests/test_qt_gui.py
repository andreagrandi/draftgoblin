from __future__ import annotations

import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from draftgoblin import __version__
from draftgoblin.audit import load_draft_audit_records
from draftgoblin.carddb import CardDatabase
from draftgoblin.pool import load_draft_state
from draftgoblin.qt_gui import (
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
    source_logo = PROJECT_ROOT / "docs" / "assets" / "draftgoblin_logo.png"
    runtime_logo = files("draftgoblin").joinpath("assets/draftgoblin_logo.png")
    assert runtime_logo.is_file()
    assert runtime_logo.read_bytes() == source_logo.read_bytes()


def test_live_gui_uses_shared_metadata_augmenting_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = CardDatabase(cards={})
    factory_calls: list[tuple[CardDatabase, Path | None, bool]] = []

    monkeypatch.setattr(
        "draftgoblin.qt_gui.load_or_refresh_card_database",
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
        "draftgoblin.qt_gui.metadata_augmenting_ratings_progress_loader",
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

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from draftgoblin import __version__
from draftgoblin.carddb import build_card_database_from_bulk_file
from draftgoblin.cardimages import CardImageService
from draftgoblin.qt_adapter import GuiPreferencesAdapter, LiveSessionAdapter
from draftgoblin.session import LiveSession


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
        (project_root / "draftgoblin" / "assets" / "draftgoblin_logo.png").read_bytes()
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
app_dir = Path(os.environ["DRAFTGOBLIN_E2E_APP_DIR"])
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
qml_directory = project_root / "draftgoblin" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draftgoblin")
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
    del engine
"""
    completed = _run_qml_probe(
        probe,
        timeout=20,
        environment={"DRAFTGOBLIN_E2E_APP_DIR": str(app_dir)},
    )

    assert completed.returncode == 0, completed.stderr


def test_qml_keyboard_controls_dispatch_account_and_ratings_commands_offscreen() -> None:
    probe = """
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtQuickControls2 import QQuickStyle

from draftgoblin import __version__
from draftgoblin.mock_session import MockLiveSession
from draftgoblin.qt_adapter import GuiPreferencesAdapter
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
preference_dir = TemporaryDirectory()
preferences = GuiPreferencesAdapter(app_dir=preference_dir.name)
engine = QQmlApplicationEngine()
qml_directory = Path.cwd() / "draftgoblin" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draftgoblin")
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
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from draftgoblin import __version__
from draftgoblin.mock_session import MockLiveSession
from draftgoblin.qt_adapter import GuiPreferencesAdapter
from draftgoblin.qt_mock import MockSessionAdapter


def find_visual_item(item: QQuickItem, object_name: str) -> QQuickItem | None:
    if item.objectName() == object_name:
        return item
    for child in item.childItems():
        found = find_visual_item(child, object_name)
        if found is not None:
            return found
    return None


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
qml_directory = Path.cwd() / "draftgoblin" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("applicationTitle", "Draftgoblin")
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
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle

from draftgoblin import __version__
from draftgoblin.mock_session import MockLiveSession
from draftgoblin.qt_adapter import GuiPreferencesAdapter
from draftgoblin.qt_mock import MockSessionAdapter
from draftgoblin.session import FocusBuildCard, RequestBacktest, RequestBuild


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
qml_directory = Path.cwd() / "draftgoblin" / "qml"
engine.addImportPath(str(qml_directory))
context = engine.rootContext()
context.setContextProperty("sessionProvider", provider)
context.setContextProperty("guiPreferences", preferences)
context.setContextProperty("applicationTitle", "Draftgoblin")
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
logo = find_visual_item(root.contentItem(), "draftgoblinLogo")
app_bar_title = root.findChild(QObject, "appBarBrandTitle")
assert logo is not None and logo.isVisible()
assert app_bar_title is not None and app_bar_title.isVisible() is False
accessible_logo = QAccessible.queryAccessibleInterface(logo)
assert accessible_logo is not None
assert accessible_logo.text(QAccessible.Text.Name) == "Draftgoblin logo"

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

root.resize(760, 900)
root.setProperty("currentSurface", "live")
application.processEvents()
live_tabs = root.findChild(QObject, "liveDetailTabs")
narrow_live_preview = find_visual_item(root.contentItem(), "narrowLiveCardPreview")
narrow_live_pool = find_visual_item(root.contentItem(), "narrowLivePoolDetails")
assert live_tabs is not None and live_tabs.property("currentIndex") == 1
assert narrow_live_preview is not None and narrow_live_preview.property("visible") is False
assert narrow_live_pool is not None and narrow_live_pool.isVisible()
assert logo.isVisible() is False
assert app_bar_title.isVisible()

preferences.setCardPreview(True)
application.processEvents()


root.setProperty("currentSurface", "build")
application.processEvents()
build_view = root.findChild(QObject, "buildView")
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

root.resize(1059, 900)
application.processEvents()
assert root.property("narrow") is False
assert build_view.property("compactPresentation") is True
compact_spell = find_visual_item(root.contentItem(), "buildSpellButton1")
assert compact_spell is not None and compact_spell.isVisible()

root.resize(1060, 900)
application.processEvents()
assert build_view.property("compactPresentation") is False

wide_curve = find_visual_item(root.contentItem(), "wideBuildManaCurve")
assert wide_curve is not None and wide_curve.isVisible()
wide_average = find_visual_item(wide_curve, "manaCurveAverage")
assert wide_average is not None and wide_average.isVisible()
assert wide_average.property("text") == "Average mana value: 2.70"
assert wide_average.width() <= wide_curve.width()
assert wide_average.width() >= wide_average.property("implicitWidth")
wide_spells = find_visual_item(root.contentItem(), "buildSpellGroups")
assert wide_spells is not None and wide_spells.isVisible()
assert wide_spells.x() >= 0
assert wide_spells.x() + wide_spells.width() <= build_view.width()
wide_reason = find_visual_item(root.contentItem(), "widePairOptionWG")
assert wide_reason is not None and wide_reason.isVisible()
assert "score 82.4" in wide_reason.property("text")
assert "25 playables" in wide_reason.property("text")

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
assert wide_fallback is not None and wide_fallback.isVisible()
assert wide_details is not None and wide_details.isVisible()
for item in (wide_heading, wide_frame, wide_fallback, wide_details):
    assert_visual_item_inside(wide_preview, item)
assert_visual_item_precedes(wide_preview, wide_curve)
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

"""
    completed = _run_qml_probe(probe)

    assert completed.returncode == 0, completed.stderr

