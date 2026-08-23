"""Launch the Draftgoblin PySide6 and QML desktop application.
Select the live application provider or deterministic mock provider explicitly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from draftgoblin.carddb import (
    CardDatabase,
    build_card_database_from_bulk_file,
    load_or_refresh_card_database,
)
from draftgoblin.cardimages import CardImageService, card_image_cache_dir
from draftgoblin.mock_session import MOCK_SCENARIOS, MockLiveSession, MockScenario
from draftgoblin.paths import resolve_player_log_path
from draftgoblin.qt_adapter import LiveSessionAdapter, SessionAdapter, SessionFactory
from draftgoblin.qt_mock import MockSessionAdapter
from draftgoblin.session import LiveSession, SnapshotPublisher
from draftgoblin.seventeen import (
    DownloadProgressCallback,
    SeventeenLandsData,
    has_cached_17lands_data,
    load_or_refresh_17lands_data,
)

SURFACES = ("live", "build", "backtest", "settings")
ProviderName = Literal["live", "mock"]


def _parser(*, forced_provider: ProviderName | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch the Draftgoblin desktop application.",
    )
    parser.add_argument(
        "--provider",
        choices=("live", "mock"),
        default="live" if forced_provider is None else forced_provider,
        help="Use production services or deterministic visual-development data.",
    )
    parser.add_argument(
        "--scenario",
        choices=MOCK_SCENARIOS,
        default="ready",
        help="Initial representative state in mock mode.",
    )
    parser.add_argument(
        "--surface",
        choices=SURFACES,
        default="live",
        help="Initial application surface.",
    )
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--log-path", type=Path, default=None)
    parser.add_argument("--app-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help="Use a local Scryfall JSONL card database in live mode.",
    )
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.set_defaults(startup_scan=True)
    parser.add_argument(
        "--no-startup-scan",
        dest="startup_scan",
        action="store_false",
        help="Skip live startup log recovery.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Render the window and exit automatically.",
    )
    parser.add_argument(
        "--smoke-test-until-complete",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Save the rendered window before exiting.",
    )
    return parser


def _live_session_factory(
    *,
    log_path: Path | None,
    app_dir: Path | None,
    bulk_file: Path | None,
    poll_interval: float,
) -> SessionFactory:
    def load_card_database() -> CardDatabase:
        if bulk_file is not None:
            return build_card_database_from_bulk_file(path=bulk_file)
        return load_or_refresh_card_database(app_dir=app_dir)

    def load_ratings(
        set_code: str,
        progress_callback: DownloadProgressCallback,
    ) -> SeventeenLandsData:
        return load_or_refresh_17lands_data(
            set_code=set_code,
            app_dir=app_dir,
            progress_callback=progress_callback,
        )

    def factory(publish: SnapshotPublisher) -> LiveSession:
        return LiveSession(
            log_path=resolve_player_log_path(log_path=log_path),
            card_database_loader=load_card_database,
            app_dir=app_dir,
            poll_interval=poll_interval,
            snapshot_publisher=publish,
            ratings_progress_loader=load_ratings,
            card_image_service=CardImageService(
                cache_dir=card_image_cache_dir(app_dir=app_dir),
                timeout_seconds=2.0,
                max_attempts=1,
            ),
            ratings_cache_checker=lambda set_code: has_cached_17lands_data(
                set_code=set_code,
                app_dir=app_dir,
            ),
        )

    return factory


def _build_provider(*, args: argparse.Namespace) -> SessionAdapter:
    if args.provider == "mock":
        return MockSessionAdapter(
            session=MockLiveSession(
                scenario=cast(MockScenario, args.scenario),
            )
        )
    if args.poll_interval <= 0:
        raise ValueError("--poll-interval must be greater than zero.")
    return LiveSessionAdapter(
        session_factory=_live_session_factory(
            log_path=args.log_path,
            app_dir=args.app_dir,
            bulk_file=args.bulk_file,
            poll_interval=args.poll_interval,
        ),
        poll_interval_ms=max(1, round(args.poll_interval * 1000)),
        startup_scan=args.startup_scan,
    )


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
        print(f"Saved GUI screenshot to {screenshot}")
    application.quit()


def _finish_smoke_test_when_draft_completes(
    *,
    engine: QQmlApplicationEngine,
    application: QGuiApplication,
    provider: SessionAdapter,
    screenshot: Path | None,
    timeout_seconds: float = 10.0,
) -> None:
    deadline = monotonic() + timeout_seconds
    timer = QTimer(application)

    def stop_waiting() -> None:
        timer.stop()
        timer.timeout.disconnect(finish_when_ready)
        timer.deleteLater()


    def finish_when_ready() -> None:
        status = provider.state.get("status", {})
        if status.get("phase") == "draft_complete":
            stop_waiting()
            QTimer.singleShot(
                0,
                lambda: _finish_smoke_test(
                    engine=engine,
                    application=application,
                    screenshot=screenshot,
                ),
            )
        elif monotonic() >= deadline:
            stop_waiting()
            QTimer.singleShot(0, lambda: application.exit(1))

    timer.setInterval(20)
    timer.timeout.connect(finish_when_ready)
    timer.start()

def run_gui(
    *,
    argv: Sequence[str] | None = None,
    forced_provider: ProviderName | None = None,
) -> int:
    args = _parser(forced_provider=forced_provider).parse_args(argv)
    if forced_provider is not None:
        args.provider = forced_provider

    QQuickStyle.setStyle("Fusion")
    application = QGuiApplication([sys.argv[0]])
    application.setApplicationName("Draftgoblin")
    application.setOrganizationName("Draftgoblin")

    provider = _build_provider(args=args)
    engine = QQmlApplicationEngine()
    qml_directory = Path(__file__).with_name("qml")
    engine.addImportPath(str(qml_directory))
    context = engine.rootContext()
    context.setContextProperty("sessionProvider", provider)
    context.setContextProperty("applicationTitle", "Draftgoblin")
    context.setContextProperty("initialSurface", args.surface)
    context.setContextProperty("initialWindowWidth", args.width)
    context.setContextProperty("initialWindowHeight", args.height)
    engine.setInitialProperties({"provider": provider})

    engine.load(QUrl.fromLocalFile(str(qml_directory / "Main.qml")))
    if not engine.rootObjects():
        return 1

    if isinstance(provider, LiveSessionAdapter):
        application.aboutToQuit.connect(provider.shutdown)
        provider.start()

    if args.smoke_test_until_complete:
        _finish_smoke_test_when_draft_completes(
            engine=engine,
            application=application,
            provider=provider,
            screenshot=args.screenshot,
        )
    elif args.smoke_test or args.screenshot is not None:
        QTimer.singleShot(
            800,
            lambda: _finish_smoke_test(
                engine=engine,
                application=application,
                screenshot=args.screenshot,
            ),
        )
    exit_code = application.exec()
    if isinstance(provider, LiveSessionAdapter):
        provider.shutdown()
    del engine
    return exit_code


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())

