"""Focused checks for native desktop deployment inputs."""

from __future__ import annotations

import configparser
import plistlib
import shlex
import tomllib
from pathlib import Path

import pytest

from tests import bundle_smoke
from tests.bundle_smoke import _resolve_bundle_executable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATHS = {
    "macos": PROJECT_ROOT / "pysidedeploy.macos.spec",
    "windows": PROJECT_ROOT / "pysidedeploy.windows.spec",
}
EXPECTED_EXCLUDED_QML_PLUGINS = {
    "QtCharts",
    "QtQuick3D",
    "QtSensors",
    "QtTest",
    "QtWebEngine",
}
EXPECTED_PRODUCT_NAME = "Draft Omen"
EXPECTED_BUNDLE_IDENTIFIER = "io.github.andreagrandi.draftomen"


def _read_spec(*, path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def _read_project_metadata() -> dict[str, object]:
    with (PROJECT_ROOT / "pyproject.toml").open(mode="rb") as project_file:
        return tomllib.load(project_file)["project"]


def _create_macos_bundle(
    *,
    root: Path,
    metadata: object,
    executable_name: str | None = "qt_gui",
) -> Path:
    bundle_path = root / "Draftomen.app"
    executable_directory = bundle_path / "Contents" / "MacOS"
    executable_directory.mkdir(parents=True)
    if executable_name is not None:
        (executable_directory / executable_name).write_bytes(b"executable")
    with (bundle_path / "Contents" / "Info.plist").open(mode="wb") as plist_file:
        plistlib.dump(metadata, plist_file)
    return bundle_path


def test_bundle_smoke_main_configures_launch_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundle smoke timeout is strict by default and configurable for CI."""

    bundle_path = tmp_path / "Draftomen.exe"
    bundle_path.write_bytes(b"executable")
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(bundle_smoke.subprocess, "run", fake_run)

    assert bundle_smoke.main([str(bundle_path)]) == 0
    assert bundle_smoke.main([str(bundle_path), "--timeout", "300"]) == 0
    assert [call["timeout"] for call in calls] == [60, 300]


def test_macos_bundle_resolution_uses_plist_executable(tmp_path: Path) -> None:
    """The plist target wins when an app contains neighboring binaries."""

    bundle_path = _create_macos_bundle(
        root=tmp_path,
        metadata={"CFBundleExecutable": "qt_gui"},
    )
    executable_directory = bundle_path / "Contents" / "MacOS"
    (executable_directory / "libpython3.12.dylib").write_bytes(b"library")
    (executable_directory / "helper").write_bytes(b"neighbor")

    assert _resolve_bundle_executable(bundle_path=bundle_path) == (
        executable_directory / "qt_gui"
    )


@pytest.mark.parametrize(
    ("metadata", "error_fragment"),
    [
        ({}, "CFBundleExecutable"),
        ({"CFBundleExecutable": ""}, "CFBundleExecutable"),
        ({"CFBundleExecutable": "   "}, "CFBundleExecutable"),
        ({"CFBundleExecutable": 42}, "CFBundleExecutable"),
        ([], "expected a dictionary"),
    ],
)
def test_macos_bundle_resolution_rejects_invalid_metadata(
    tmp_path: Path,
    metadata: object,
    error_fragment: str,
) -> None:
    """Malformed executable metadata fails instead of guessing a file."""

    bundle_path = _create_macos_bundle(root=tmp_path, metadata=metadata)

    with pytest.raises(RuntimeError, match=error_fragment):
        _resolve_bundle_executable(bundle_path=bundle_path)


def test_macos_bundle_resolution_rejects_missing_metadata(tmp_path: Path) -> None:
    """An app without Info.plist cannot be resolved."""

    bundle_path = tmp_path / "Draftomen.app"
    (bundle_path / "Contents" / "MacOS").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="Missing macOS bundle metadata"):
        _resolve_bundle_executable(bundle_path=bundle_path)


def test_macos_bundle_resolution_rejects_missing_executable(tmp_path: Path) -> None:
    """A valid plist target must exist as a regular file."""

    bundle_path = _create_macos_bundle(
        root=tmp_path,
        metadata={"CFBundleExecutable": "qt_gui"},
        executable_name=None,
    )

    with pytest.raises(RuntimeError, match="does not exist as a regular file"):
        _resolve_bundle_executable(bundle_path=bundle_path)


def test_macos_bundle_resolution_rejects_malformed_plist(tmp_path: Path) -> None:
    """An unreadable Info.plist cannot supply an executable name."""

    bundle_path = tmp_path / "Draftomen.app"
    contents_directory = bundle_path / "Contents"
    (contents_directory / "MacOS").mkdir(parents=True)
    (contents_directory / "Info.plist").write_bytes(b"not a plist")

    with pytest.raises(RuntimeError, match="Could not read macOS bundle metadata"):
        _resolve_bundle_executable(bundle_path=bundle_path)


def test_windows_bundle_resolution_uses_exe_directly(tmp_path: Path) -> None:
    """Windows bundles continue to resolve their direct executable path."""

    executable = tmp_path / "Draftomen.EXE"
    executable.write_bytes(b"executable")
    (tmp_path / "Draftomen.dll").write_bytes(b"library")

    assert _resolve_bundle_executable(bundle_path=executable) == executable


def test_native_specs_preserve_project_metadata() -> None:
    """Nuitka metadata stays aligned with the package version and branding."""

    project = _read_project_metadata()
    project_version = project["version"]
    assert project_version == "0.3.1"
    expected_common_args = {
        "--company-name=Draft Omen",
        f"--product-name={EXPECTED_PRODUCT_NAME}",
        f"--file-version={project_version}",
        f"--product-version={project_version}",
    }

    for platform, spec_path in SPEC_PATHS.items():
        spec = _read_spec(path=spec_path)
        nuitka_args = set(shlex.split(spec["nuitka"]["extra_args"]))
        assert expected_common_args <= nuitka_args
        if platform == "macos":
            assert {
                f"--macos-app-name={EXPECTED_PRODUCT_NAME}",
                f"--macos-app-version={project_version}",
                f"--macos-signed-app-name={EXPECTED_BUNDLE_IDENTIFIER}",
            } <= nuitka_args
        else:
            assert "--assume-yes-for-downloads" not in nuitka_args
            assert (
                "--file-description=An unofficial Quick Draft assistant for MTG Arena"
                in nuitka_args
            )


def test_native_specs_enumerate_runtime_inputs() -> None:
    """Both platform specs describe the same app inputs and unsigned outputs."""

    with (PROJECT_ROOT / "pyproject.toml").open(mode="rb") as project_file:
        project_files = tomllib.load(project_file)["tool"]["pyside6-project"]["files"]
    expected_qml_files = {
        path for path in project_files if path.startswith("draftomen/qml/")
    }

    for platform, spec_path in SPEC_PATHS.items():
        spec = _read_spec(path=spec_path)
        app = spec["app"]
        python = spec["python"]
        qt = spec["qt"]
        nuitka = spec["nuitka"]

        assert app["title"] == f"Draftomen-unsigned-{platform}"
        assert "unsigned" in app["exec_directory"]
        assert (PROJECT_ROOT / app["icon"]).is_file()
        assert app["project_file"] == "pyproject.toml"
        assert python["packages"] == "Nuitka==4.1.3"
        assert set(qt["qml_files"].split(",")) == expected_qml_files
        assert qt["modules"].split(",") == [
            "Core",
            "Gui",
            "Qml",
            "Quick",
            "QuickControls2",
        ]
        assert set(qt["excluded_qml_plugins"].split(",")) == EXPECTED_EXCLUDED_QML_PLUGINS
        assert set(qt["plugins"].split(",")) == {
            "imageformats",
            "platforms",
            "platformthemes",
            "styles",
        }
        assert nuitka["mode"] == "onefile"


def test_project_metadata_includes_package_sources_logo_and_no_fonts() -> None:
    """The pyside6-project input list covers package sources and assets."""

    with (PROJECT_ROOT / "pyproject.toml").open(mode="rb") as project_file:
        project_files = tomllib.load(project_file)["tool"]["pyside6-project"]["files"]

    declared_python_files = {
        path
        for path in project_files
        if Path(path).parent == Path("draftomen") and Path(path).suffix == ".py"
    }
    actual_python_files = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "draftomen").glob("*.py")
    }
    assert declared_python_files == actual_python_files

    assert "draftomen/assets/draftomen_logo.png" in project_files
    assert not any("font" in path.lower() for path in project_files)
    for path in project_files:
        assert (PROJECT_ROOT / path).is_file(), path

    assert (PROJECT_ROOT / "draftomen/assets/draftomen.icns").read_bytes()[:4] == b"icns"
    assert (PROJECT_ROOT / "draftomen/assets/draftomen.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
