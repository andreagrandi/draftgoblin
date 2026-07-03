"""Command-line interface for Draftgoblin.
Define parser wiring and temporary command stubs.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from draftgoblin import DISCLAIMER, __version__
from draftgoblin.config import COLOR_PAIRS
from draftgoblin.paths import (
    UnsupportedPlatformError,
    app_data_dir,
    resolve_player_log_path,
)

CommandHandler = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.
    Keep subcommand registration centralized for CLI tests.
    """

    parser = argparse.ArgumentParser(
        prog="draftgoblin",
        description="Unofficial Quick Draft assistant for MTG Arena.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the Draftgoblin version and required Fan Content disclaimer.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
        title="subcommands",
    )

    watch_parser = subparsers.add_parser(
        name="watch",
        help="Watch Player.log and show live draft recommendations.",
        description="Stub for the future live log watcher and TUI entry point.",
    )
    watch_parser.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="Override the default MTG Arena Player.log path.",
    )
    watch_parser.add_argument(
        "--plain",
        action="store_true",
        help="Use plain-text output instead of the future TUI.",
    )
    watch_parser.set_defaults(handler=handle_watch)

    replay_parser = subparsers.add_parser(
        name="replay",
        help="Replay a captured Player.log fixture in plain-text mode.",
        description="Stub for deterministic offline replay over a captured log file.",
    )
    replay_parser.add_argument(
        "logfile",
        type=Path,
        help="Captured Player.log file to replay.",
    )
    replay_parser.set_defaults(handler=handle_replay)

    build_parser_command = subparsers.add_parser(
        name="build",
        help="Build a deck from a persisted or exported draft pool.",
        description="Stub for the future 40-card deck builder.",
    )
    build_parser_command.add_argument(
        "--pool",
        type=Path,
        default=None,
        help="Pool file to build from once pool persistence exists.",
    )
    build_parser_command.add_argument(
        "--account",
        default=None,
        help="MTGA account identifier to disambiguate persisted pools.",
    )
    build_parser_command.add_argument(
        "--pair",
        choices=COLOR_PAIRS,
        default=None,
        help="Force a two-color pair when building.",
    )
    build_parser_command.add_argument(
        "--allow-splash",
        action="store_true",
        help="Allow splash cards once the deck builder implements splash logic.",
    )
    build_parser_command.set_defaults(handler=handle_build)

    refresh_parser = subparsers.add_parser(
        name="refresh-data",
        help="Refresh cached Scryfall and 17lands data.",
        description="Stub for manual static-data cache refreshes.",
    )
    refresh_parser.set_defaults(handler=handle_refresh_data)

    return parser


def format_version() -> str:
    """Format the version banner.
    Include the required Fan Content and 17Lands disclaimer block.
    """

    return f"draftgoblin {__version__}\n\n{DISCLAIMER}"


def handle_watch(args: argparse.Namespace) -> int:
    """Handle the watch command stub.
    Resolve Player.log now so --log-path wiring is covered by tests.
    """

    try:
        log_path = resolve_player_log_path(log_path=args.log_path)
    except UnsupportedPlatformError as error:
        print(f"watch stub: {error}", file=sys.stderr)
        return 2

    mode = "plain-text" if args.plain else "TUI"
    print(f"watch stub: would monitor {log_path} in {mode} mode.")
    return 0


def handle_replay(args: argparse.Namespace) -> int:
    """Handle the replay command stub.
    Keep the command callable while parser and event modules are pending.
    """

    print(f"replay stub: would replay {args.logfile}.")
    return 0


def handle_build(args: argparse.Namespace) -> int:
    """Handle the build command stub.
    Keep flags in place for later pool and deck-builder work.
    """

    pool = args.pool if args.pool is not None else "the latest persisted pool"
    pair = args.pair if args.pair is not None else "auto-selected pair"
    splash = "with splash enabled" if args.allow_splash else "without splash"
    account = args.account if args.account is not None else "active account"
    print(f"build stub: would build {pool} for {account} using {pair} {splash}.")
    return 0


def handle_refresh_data(args: argparse.Namespace) -> int:
    """Handle the refresh-data command stub.
    Show the cache root that later data refreshes will use.
    """

    del args
    print(f"refresh-data stub: would refresh caches under {app_data_dir()}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Draftgoblin CLI.
    Return a process-style exit code for tests and console-script use.
    """

    parser = build_parser()
    args = parser.parse_args(args=argv)

    if args.version:
        print(format_version())
        return 0

    handler: CommandHandler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
