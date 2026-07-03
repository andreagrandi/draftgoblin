"""Command-line interface for Draftgoblin.
Define parser wiring and command handlers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from draftgoblin import DISCLAIMER, __version__
from draftgoblin.carddb import (
    CardDatabase,
    CardDatabaseError,
    build_card_database_from_bulk_file,
    card_database_cache_path,
    load_card_database,
    refresh_card_database,
)
from draftgoblin.config import COLOR_PAIRS
from draftgoblin.deckbuilder import (
    DeckBuilderError,
    format_build_result,
    load_persisted_pool,
    load_pool_file,
    select_color_pair,
    select_deck_spells,
)
from draftgoblin.events import DraftLogParseError
from draftgoblin.logfollow import LogFollowError
from draftgoblin.paths import UnsupportedPlatformError, resolve_player_log_path
from draftgoblin.pool import DraftPoolError
from draftgoblin.replay import ReplayError, replay_log_file
from draftgoblin.seventeen import (
    SeventeenLandsError,
    load_cached_17lands_data,
    load_or_refresh_17lands_data,
)
from draftgoblin.watch import run_plain_watch

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
        description="Live log watcher. Plain mode streams replay-compatible text.",
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
    watch_parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Resolve card names from a local Scryfall JSONL(.gz) bulk file "
            "instead of the cached card database."
        ),
    )
    watch_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between Player.log polls in watch mode.",
    )
    watch_parser.add_argument(
        "--startup-scan",
        action="store_true",
        help="Scan Player-prev.log and Player.log at startup before tailing.",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Process one poll cycle and exit.",
    )
    watch_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    watch_parser.set_defaults(handler=handle_watch)

    replay_parser = subparsers.add_parser(
        name="replay",
        help="Replay a captured Player.log fixture in plain-text mode.",
        description="Deterministic offline replay over a captured log file.",
    )
    replay_parser.add_argument(
        "logfile",
        type=Path,
        help="Captured Player.log file to replay.",
    )
    replay_parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Resolve card names from a local Scryfall JSONL(.gz) bulk file "
            "instead of the cached card database."
        ),
    )
    replay_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    replay_parser.set_defaults(handler=handle_replay)

    build_parser_command = subparsers.add_parser(
        name="build",
        help="Build a deck from a persisted or exported draft pool.",
        description="Select a color pair and constrained spell list from a drafted pool.",
    )
    build_parser_command.add_argument(
        "--pool",
        type=Path,
        default=None,
        help="JSON pool file to build from instead of persisted state.",
    )
    build_parser_command.add_argument(
        "--account",
        default=None,
        help="MTGA account identifier to disambiguate persisted pools.",
    )
    build_parser_command.add_argument(
        "--draft-id",
        default=None,
        help="Draft identifier to disambiguate persisted pools.",
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
        help="Accepted but inert in v1; splash selection is deferred.",
    )
    build_parser_command.add_argument(
        "--set-code",
        default=None,
        help="Set code for simple --pool files that do not include one.",
    )
    build_parser_command.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Resolve card names from a local Scryfall JSONL(.gz) bulk file "
            "instead of the cached card database."
        ),
    )
    build_parser_command.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    build_parser_command.set_defaults(handler=handle_build)

    refresh_parser = subparsers.add_parser(
        name="refresh-data",
        help="Refresh cached Scryfall card metadata.",
        description="Refresh the local Arena grpId card metadata cache from Scryfall.",
    )
    refresh_parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Build the card metadata cache from a local Scryfall JSONL(.gz) "
            "file instead of downloading."
        ),
    )
    refresh_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    refresh_parser.set_defaults(handler=handle_refresh_data)

    return parser


def format_version() -> str:
    """Format the version banner.
    Include the required Fan Content and 17Lands disclaimer block.
    """

    return f"draftgoblin {__version__}\n\n{DISCLAIMER}"


def handle_watch(args: argparse.Namespace) -> int:
    """Handle live Player.log watching.
    Plain mode follows the log and renders replay-compatible pack output.
    """

    try:
        log_path = resolve_player_log_path(log_path=args.log_path)
    except UnsupportedPlatformError as error:
        print(f"watch failed: {error}", file=sys.stderr)
        return 2

    if not args.plain:
        print(
            "watch TUI is not implemented yet; rerun with --plain for live text output.",
            file=sys.stderr,
        )
        return 2

    if args.poll_interval <= 0:
        print("watch failed: --poll-interval must be greater than zero.", file=sys.stderr)
        return 2

    try:
        database = _load_watch_card_database(args=args)
        return run_plain_watch(
            log_path=log_path,
            card_database=database,
            app_dir=args.app_dir,
            poll_interval=args.poll_interval,
            once=args.once,
            startup_scan=args.startup_scan,
            ratings_loader=lambda set_code: load_or_refresh_17lands_data(
                set_code=set_code,
                app_dir=args.app_dir,
            ),
        )
    except KeyboardInterrupt:
        return 130
    except (
        CardDatabaseError,
        DraftLogParseError,
        DraftPoolError,
        LogFollowError,
        SeventeenLandsError,
    ) as error:
        print(f"watch failed: {error}", file=sys.stderr)
        return 1


def _load_watch_card_database(*, args: argparse.Namespace) -> CardDatabase:
    """Load watch card metadata without implicit network access.
    Users can run refresh-data first or pass a local bulk file for tests.
    """

    return _load_replay_card_database(args=args)


def handle_replay(args: argparse.Namespace) -> int:
    """Handle deterministic offline replay.
    Card metadata is loaded only from cache or an explicitly supplied bulk file.
    """

    try:
        database = _load_replay_card_database(args=args)
        output = replay_log_file(
            logfile=args.logfile,
            card_database=database,
            ratings_loader=lambda set_code: load_cached_17lands_data(
                set_code=set_code,
                app_dir=args.app_dir,
            ),
        )
    except (
        CardDatabaseError,
        DraftLogParseError,
        DraftPoolError,
        ReplayError,
        SeventeenLandsError,
    ) as error:
        print(f"replay failed: {error}", file=sys.stderr)
        return 1

    print(output, end="")
    return 0


def _load_replay_card_database(*, args: argparse.Namespace) -> CardDatabase:
    """Load replay card metadata without network access.
    The vendored bulk path is useful for fixture and CI regression checks.
    """

    if args.bulk_file is not None:
        return build_card_database_from_bulk_file(path=args.bulk_file)

    return load_card_database(app_dir=args.app_dir)


def handle_build(args: argparse.Namespace) -> int:
    """Handle deck-builder pair selection and constrained spell fill.
    The command is fully offline, using persisted pools and local caches.
    """

    try:
        database = _load_replay_card_database(args=args)
        pool = (
            load_pool_file(path=args.pool, set_code=args.set_code)
            if args.pool is not None
            else load_persisted_pool(
                app_dir=args.app_dir,
                account_id=args.account,
                draft_id=args.draft_id,
            )
        )
        ratings_data = load_cached_17lands_data(
            set_code=pool.set_code,
            app_dir=args.app_dir,
        )
        selection = select_color_pair(
            pool_grp_ids=pool.pool_grp_ids,
            card_database=database,
            ratings_data=ratings_data,
            forced_pair=args.pair,
        )
        spell_selection = select_deck_spells(
            pool_grp_ids=pool.pool_grp_ids,
            card_database=database,
            pair=selection.chosen.pair,
            ratings_data=ratings_data,
            allow_splash=args.allow_splash,
        )
    except (CardDatabaseError, DeckBuilderError, DraftPoolError, SeventeenLandsError) as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1

    print(
        format_build_result(
            pool=pool,
            selection=selection,
            spell_selection=spell_selection,
        ),
        end="",
    )
    return 0


def handle_refresh_data(args: argparse.Namespace) -> int:
    """Handle the refresh-data command.
    Build the local Scryfall-backed grpId metadata cache.
    """

    try:
        database = refresh_card_database(
            app_dir=args.app_dir,
            bulk_file=args.bulk_file,
        )
    except CardDatabaseError as error:
        print(f"refresh-data failed: {error}", file=sys.stderr)
        return 1

    cache_path = card_database_cache_path(app_dir=args.app_dir)
    print(f"refreshed {len(database)} card records at {cache_path}.")
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
