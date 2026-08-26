"""Launch a compiled Draftomen desktop bundle with deterministic mock data.
This helper intentionally imports only the standard library so the target bundle is
what supplies the application and PySide6 runtime.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory


def build_parser() -> argparse.ArgumentParser:
    """Build the native bundle smoke-test argument parser."""

    parser = argparse.ArgumentParser(
        description="Launch a compiled Draftomen bundle in deterministic mock mode.",
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Maximum seconds to wait for the compiled application to exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Launch one platform bundle and return a process-style exit code."""

    args = build_parser().parse_args(args=argv)
    bundle_path = args.bundle.resolve()
    executable = _resolve_bundle_executable(bundle_path=bundle_path)

    with TemporaryDirectory(prefix="draftomen-bundle-smoke-") as temporary_dir:
        command = [
            str(executable),
            "--provider",
            "mock",
            "--smoke-test",
            "--app-dir",
            str(Path(temporary_dir) / "app"),
        ]
        environment = _clean_environment(environment=os.environ)
        subprocess.run(
            args=command,
            check=True,
            cwd=executable.parent,
            env=environment,
            timeout=args.timeout,
        )

    return 0


def _resolve_bundle_executable(*, bundle_path: Path) -> Path:
    """Resolve the executable named by a macOS app or a Windows bundle."""

    if bundle_path.suffix.lower() == ".app" and bundle_path.is_dir():
        contents_directory = bundle_path / "Contents"
        plist_path = contents_directory / "Info.plist"
        if not plist_path.is_file():
            raise RuntimeError(f"Missing macOS bundle metadata: {plist_path}")

        try:
            with plist_path.open(mode="rb") as plist_file:
                metadata = plistlib.load(plist_file)
        except (OSError, plistlib.InvalidFileException, TypeError, ValueError) as error:
            raise RuntimeError(
                f"Could not read macOS bundle metadata from {plist_path}: {error}"
            ) from error

        if not isinstance(metadata, dict):
            raise RuntimeError(
                f"Invalid macOS bundle metadata in {plist_path}: "
                "expected a dictionary."
            )

        executable_name = metadata.get("CFBundleExecutable")
        if not isinstance(executable_name, str) or not executable_name.strip():
            raise RuntimeError(
                f"Invalid macOS bundle metadata in {plist_path}: "
                "CFBundleExecutable must be a nonempty string."
            )

        executable_path = contents_directory / "MacOS" / executable_name
        if not executable_path.is_file():
            raise RuntimeError(
                f"macOS bundle executable does not exist as a regular file: "
                f"{executable_path}"
            )
        return executable_path

    if bundle_path.suffix.lower() == ".exe" and bundle_path.is_file():
        return bundle_path

    raise RuntimeError(
        f"Expected a macOS .app directory or Windows .exe file: {bundle_path}"
    )


def _clean_environment(*, environment: Mapping[str, str]) -> dict[str, str]:
    """Remove development-environment overrides from the bundle process."""

    clean_environment = dict(environment)
    for variable in (
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
    ):
        clean_environment.pop(variable, None)
    return clean_environment


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
