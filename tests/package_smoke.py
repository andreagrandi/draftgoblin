"""Smoke-test an installable Draftomen distribution artifact.
Exercise the packaged console command in an isolated uv tool environment.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from glob import glob
from pathlib import Path
from tarfile import TarFile
from tempfile import TemporaryDirectory
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "quick-draft-msh-player.log"
)
BULK_FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "scryfall-default-cards-sample.jsonl"
)
REPLAY_GOLDEN_PATH = (
    PROJECT_ROOT / "tests" / "golden" / "quick-draft-msh-player.replay.txt"
)
RUNTIME_LOGO_PATH = "draftomen/assets/draftomen_logo.png"


def build_parser() -> argparse.ArgumentParser:
    """Build the distribution smoke-test argument parser.
    Accept one repository-relative artifact pattern.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_pattern")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Install and exercise one distribution artifact.
    Return a process-style exit code for release automation.
    """

    args = build_parser().parse_args(args=argv)
    artifact_path = _resolve_artifact(pattern=args.artifact_pattern)
    _check_runtime_logo(artifact_path=artifact_path)

    with TemporaryDirectory(prefix="draftomen-package-smoke-") as temporary_dir:
        temporary_path = Path(temporary_dir)
        environment = os.environ.copy()
        environment["UV_TOOL_DIR"] = str(temporary_path / "tools")
        environment["UV_TOOL_BIN_DIR"] = str(temporary_path / "bin")

        subprocess.run(
            args=[
                "uv",
                "tool",
                "install",
                "--python",
                "3.12",
                "--from",
                str(artifact_path),
                "draftomen",
            ],
            check=True,
            env=environment,
        )

        executable_path = temporary_path / "bin" / _executable_name()
        version_output = _run_command(
            command=[str(executable_path), "--version"],
            environment=environment,
        )
        _check_version(output=version_output)

        replay_output = _run_command(
            command=[
                str(executable_path),
                "replay",
                str(REPLAY_FIXTURE_PATH),
                "--bulk-file",
                str(BULK_FIXTURE_PATH),
                "--app-dir",
                str(temporary_path / "app"),
            ],
            environment=environment,
        )
        expected_replay_output = REPLAY_GOLDEN_PATH.read_text(encoding="utf-8")
        if replay_output != expected_replay_output:
            raise RuntimeError("Packaged replay output does not match the golden file.")

    return 0


def _check_runtime_logo(*, artifact_path: Path) -> None:
    """Check the distribution artifact for the desktop logo.
    Require the installed runtime resource used by QML.
    """
    if artifact_path.suffix == ".whl":
        with ZipFile(file=artifact_path) as artifact:
            paths = artifact.namelist()
    else:
        with TarFile.open(name=artifact_path) as artifact:
            paths = artifact.getnames()

    if not any(path.endswith(RUNTIME_LOGO_PATH) for path in paths):
        raise RuntimeError(
            f"Packaged artifact omitted the desktop logo: {RUNTIME_LOGO_PATH}."
        )


def _resolve_artifact(*, pattern: str) -> Path:
    """Resolve exactly one distribution artifact.
    Accept repository-relative and absolute artifact patterns.
    """

    search_pattern = pattern
    if not Path(pattern).is_absolute():
        search_pattern = str(PROJECT_ROOT / pattern)

    matches = sorted(Path(match) for match in glob(pathname=search_pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one artifact matching {pattern!r}, found {len(matches)}."
        )

    return matches[0].resolve()


def _executable_name() -> str:
    """Return the installed console-script filename.
    Include the Windows executable suffix when required.
    """

    if os.name == "nt":
        return "draftomen.exe"

    return "draftomen"


def _run_command(*, command: Sequence[str], environment: dict[str, str]) -> str:
    """Run one packaged command and capture its output.
    Raise immediately when the command exits unsuccessfully.
    """

    result = subprocess.run(
        args=command,
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return result.stdout


def _check_version(*, output: str) -> None:
    """Check packaged version output against project metadata.
    Require the user-facing Fan Content disclaimer as well.
    """

    with (PROJECT_ROOT / "pyproject.toml").open(mode="rb") as project_file:
        project = tomllib.load(project_file)
    expected_version = project["project"]["version"]

    if f"draftomen {expected_version}\n" not in output:
        raise RuntimeError("Packaged command reports an unexpected version.")
    if "Draft Omen is unofficial Fan Content" not in output:
        raise RuntimeError("Packaged command omitted the Fan Content disclaimer.")


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
