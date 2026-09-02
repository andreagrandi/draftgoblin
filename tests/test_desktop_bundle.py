"""Focused checks for native desktop deployment inputs."""

from __future__ import annotations

import configparser
import gzip
import hashlib
import json
import plistlib
import shlex
import tomllib
from pathlib import Path

import pytest

from draftomen.set_profile import load_set_profile

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


BASELINE_PROFILE_RESOURCE = "draftomen/baseline_profiles/hob-quickdraft.json"
BASELINE_PROFILE_PATH = PROJECT_ROOT / BASELINE_PROFILE_RESOURCE
BASELINE_PROFILE_MAPPING = f"--include-data-files={BASELINE_PROFILE_RESOURCE}={BASELINE_PROFILE_RESOURCE}"
SELECTED_GZIP_SHA256 = "3ea8e91d02b63a724016ec5dd45b5d1b53ff0aecdad1feda6e495c588d831447"
SELECTED_SOURCE_BYTES = 12997
SELECTED_SOURCE_SHA256 = "362fb91ba62f3d5a324eb21371102a6e9b9835be88553ed69a076a741fcb7302"
SELECTED_GZIP_PATH = (
    PROJECT_ROOT
    / "profile-snapshots"
    / "hob-quickdraft"
    / SELECTED_GZIP_SHA256
    / f"{SELECTED_GZIP_SHA256}.json.gz"
)


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
    """The smoke command uses an isolated app directory and strict timeout."""

    bundle_path = tmp_path / "Draftomen.exe"
    bundle_path.write_bytes(b"executable")
    calls: list[dict[str, object]] = []
    app_directories: list[Path] = []

    def fake_run(**kwargs: object) -> None:
        command = kwargs["args"]
        assert isinstance(command, list)
        assert command[:-1] == [
            str(bundle_path.resolve()),
            "--provider",
            "mock",
            "--smoke-test",
            "--verify-bundled-profile",
            "--app-dir",
        ]
        app_directory = Path(command[-1])
        assert app_directory.name == "app"
        assert app_directory.parent.is_dir()
        assert app_directory.parent.name.startswith("draftomen-bundle-smoke-")
        cache_path = app_directory / "set-profiles" / "hob-quickdraft.json"
        assert not cache_path.exists()
        assert not cache_path.is_symlink()
        app_directories.append(app_directory)
        calls.append(kwargs)

    monkeypatch.setattr(bundle_smoke.subprocess, "run", fake_run)

    assert bundle_smoke.main([str(bundle_path)]) == 0
    assert bundle_smoke.main([str(bundle_path), "--timeout", "300"]) == 0
    assert [call["timeout"] for call in calls] == [60, 300]
    assert len(app_directories) == 2
    assert len({path.parent for path in app_directories}) == 2


def test_bundle_smoke_main_rejects_flat_profile_cache_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful bundle launch must not populate the flat profile cache."""

    bundle_path = tmp_path / "Draftomen.exe"
    bundle_path.write_bytes(b"executable")

    def fake_run(**kwargs: object) -> None:
        command = kwargs["args"]
        assert isinstance(command, list)
        app_directory = Path(command[-1])
        cache_path = app_directory / "set-profiles" / "hob-quickdraft.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(b"unexpected cache entry")

    monkeypatch.setattr(bundle_smoke.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="mutated the flat profile cache"):
        bundle_smoke.main([str(bundle_path)])


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


def test_bundled_profile_provenance_and_packaging_are_deterministic(tmp_path: Path) -> None:
    """The selected baseline retains deterministic provenance and packaging
    identity across source metadata and native deployment inputs.
    """

    generation_path = SELECTED_GZIP_PATH.parent / "generation.json"
    source_path = SELECTED_GZIP_PATH.parent / "source.json"
    generation_bytes = generation_path.read_bytes()
    generation = json.loads(generation_bytes)
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    assert len(source_bytes) == SELECTED_SOURCE_BYTES
    assert hashlib.sha256(source_bytes).hexdigest() == SELECTED_SOURCE_SHA256
    generation_checksums = generation["checksums"]
    source_artifacts = source["artifacts"]
    source_gzip = source_artifacts["gzip"]
    source_profile = source_artifacts["profile"]
    source_generation = source_artifacts["generation"]
    source_environment = source["batch"]["document"]["environments"][0]

    assert SELECTED_GZIP_PATH.is_file()
    assert SELECTED_GZIP_PATH.parent.parent.name == "hob-quickdraft"
    assert SELECTED_GZIP_PATH.parent.name == SELECTED_GZIP_SHA256
    assert SELECTED_GZIP_PATH.name == f"{SELECTED_GZIP_SHA256}.json.gz"
    assert len(generation_bytes) == source["generation_bytes"] == source_generation["bytes"]
    assert hashlib.sha256(generation_bytes).hexdigest() == source["generation_sha256"] == source_generation["sha256"]

    gzip_bytes = SELECTED_GZIP_PATH.read_bytes()
    assert SELECTED_GZIP_PATH.stat().st_size == generation["gzip_bytes"] == source_gzip["bytes"]
    assert hashlib.sha256(gzip_bytes).hexdigest() == SELECTED_GZIP_SHA256
    assert generation["gzip_sha256"] == generation_checksums["gzip"] == source_gzip["sha256"]
    assert source_environment["artifacts"]["gzip"] == source_gzip

    profile_bytes = gzip.decompress(gzip_bytes)
    profile_document = json.loads(profile_bytes)
    assert len(profile_bytes) == generation["profile_bytes"] == source_profile["bytes"]
    assert (
        hashlib.sha256(profile_bytes).hexdigest()
        == generation["profile_sha256"]
        == generation_checksums["profile"]
        == source_profile["sha256"]
    )
    assert source_environment["artifacts"]["profile"] == source_profile
    assert profile_bytes == BASELINE_PROFILE_PATH.read_bytes()

    assert profile_document["set_code"] == generation["set_code"] == "hob"
    assert profile_document["format"] == generation["event_format"] == "quickdraft"
    assert profile_document["generated_at"] == generation["generated_at"]
    assert profile_document["maturity"] == "metadata-only"
    identity = source["identity"]
    assert identity["canonical_set_code"] == profile_document["set_code"]
    assert identity["canonical_event_format"] == profile_document["format"]
    assert identity["external_set_code"] == "HOB"
    assert identity["external_event_format"] == "QuickDraft"

    raw_profile_path = tmp_path / "hob-quickdraft.json"
    raw_profile_path.write_bytes(profile_bytes)
    profile = load_set_profile(
        raw_profile_path,
        expected_set_code="hob",
        expected_format="quickdraft",
    )
    assert profile.set_code == "hob"
    assert profile.event_format == "quickdraft"
    assert profile.to_bytes() == profile_bytes

    with (PROJECT_ROOT / "pyproject.toml").open(mode="rb") as project_file:
        project_config = tomllib.load(project_file)
    project_tools = project_config["tool"]
    pyside_files = project_tools["pyside6-project"]["files"]
    assert BASELINE_PROFILE_RESOURCE in pyside_files
    package_data_entries = [
        entry
        for entries in project_tools["setuptools"]["package-data"].values()
        for entry in entries
    ]
    assert not any("baseline_profiles" in entry for entry in package_data_entries)

    spec_mappings = {
        platform: tuple(
            argument
            for argument in shlex.split(_read_spec(path=spec_path)["nuitka"]["extra_args"])
            if argument.startswith("--include-data-files=")
        )
        for platform, spec_path in SPEC_PATHS.items()
    }
    assert spec_mappings == {
        "macos": (BASELINE_PROFILE_MAPPING,),
        "windows": (BASELINE_PROFILE_MAPPING,),
    }
