"""Command-line interface for Draftgoblin.
Define parser wiring and command handlers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from draftgoblin import DISCLAIMER, __version__
from draftgoblin.backtest import (
    BacktestError,
    format_backtest_report,
    generate_backtest_report,
    load_persisted_backtest_state,
)
from draftgoblin.carddb import (
    CardDatabase,
    CardDatabaseError,
    CardMetadataSeed,
    augment_card_database_with_mtgjson_set,
    build_card_database_from_bulk_file,
    card_database_cache_path,
    load_card_database,
    load_or_refresh_card_database,
    refresh_card_database,
    save_card_database,
)
from draftgoblin.config import COLOR_PAIRS
from draftgoblin.deckbuilder import (
    DeckBuilderError,
    build_deck_from_pool,
    format_build_result,
    load_persisted_pool,
    load_pool_file,
)
from draftgoblin.events import DraftLogParseError
from draftgoblin.logfollow import LogFollowError
from draftgoblin.paths import UnsupportedPlatformError, resolve_player_log_path
from draftgoblin.pool import DraftPoolError
from draftgoblin.ranking import DEFAULT_RANKING_MODE, RANKING_MODES
from draftgoblin.replay import ReplayError, replay_log_file
from draftgoblin.seventeen import (
    QUICK_DRAFT_FORMAT,
    ResolvedCardRating,
    SeventeenLandsData,
    SeventeenLandsError,
    load_cached_17lands_data,
    load_or_refresh_17lands_data,
    refresh_17lands_structure_targets,
    seventeen_lands_structure_targets_cache_path,
)
from draftgoblin.tui import run_tui_watch
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
        help="Use plain-text output instead of the default TUI.",
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
    watch_parser.set_defaults(startup_scan=True)
    watch_parser.add_argument(
        "--startup-scan",
        dest="startup_scan",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    watch_parser.add_argument(
        "--no-startup-scan",
        dest="startup_scan",
        action="store_false",
        help="Skip startup recovery and only process new Player.log lines.",
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
        description="Select a color pair, spells, lands, and bench from a drafted pool.",
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
        help=(
            "Allow up to two elite off-pair splash cards when the pool has "
            "at least two fixing sources."
        ),
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

    backtest_parser = subparsers.add_parser(
        name="backtest",
        help="Replay saved picks and compare recommendations to actual choices.",
        description=(
            "Dry-run a persisted draft with the current pick engine, using saved "
            "offered card history and the pool before each pick."
        ),
    )
    backtest_parser.add_argument(
        "--account",
        default=None,
        help="MTGA account identifier to disambiguate persisted drafts.",
    )
    backtest_parser.add_argument(
        "--draft-id",
        default=None,
        help="Draft identifier to disambiguate persisted drafts.",
    )
    backtest_parser.add_argument(
        "--ranking",
        choices=RANKING_MODES,
        default=DEFAULT_RANKING_MODE,
        help="Ranking used for recommendations (default: 17L WR).",
    )
    backtest_parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Resolve card names from a local Scryfall JSONL(.gz) bulk file "
            "instead of the cached card database."
        ),
    )
    backtest_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    backtest_parser.set_defaults(handler=handle_backtest)

    refresh_parser = subparsers.add_parser(
        name="refresh-data",
        help="Refresh cached Scryfall and Arena card metadata.",
        description=(
            "Refresh the local Arena grpId card metadata cache from Scryfall, "
            "overlaying MTG Arena local data when available."
        ),
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

    structure_parser = subparsers.add_parser(
        name="refresh-structure-targets",
        help="Compute cached 17Lands trophy-deck structure targets.",
        description=(
            "Compute per-pair deck-structure targets from a 17Lands public "
            "draft-data dump."
        ),
    )
    structure_parser.add_argument(
        "--set-code",
        required=True,
        help="Set code for the public draft data dump.",
    )
    structure_parser.add_argument(
        "--format",
        default=QUICK_DRAFT_FORMAT,
        help=f"17Lands event format (default: {QUICK_DRAFT_FORMAT}).",
    )
    structure_parser.add_argument(
        "--draft-data-file",
        type=Path,
        default=None,
        help="Local 17Lands public draft-data CSV(.gz) or tar.gz file to analyze.",
    )
    structure_parser.add_argument(
        "--draft-data-url",
        default=None,
        help=argparse.SUPPRESS,
    )
    structure_parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Resolve card names from a local Scryfall JSONL(.gz) bulk file "
            "instead of the cached card database."
        ),
    )
    structure_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    structure_parser.set_defaults(handler=handle_refresh_structure_targets)

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

    if args.poll_interval <= 0:
        print("watch failed: --poll-interval must be greater than zero.", file=sys.stderr)
        return 2

    try:
        database = _load_watch_card_database(args=args)
        ratings_loader = _metadata_augmenting_ratings_loader(
            args=args,
            database=database,
            load_ratings=lambda set_code: load_or_refresh_17lands_data(
                set_code=set_code,
                app_dir=args.app_dir,
            ),
        )
        if args.plain:
            return run_plain_watch(
                log_path=log_path,
                card_database=database,
                app_dir=args.app_dir,
                poll_interval=args.poll_interval,
                once=args.once,
                startup_scan=args.startup_scan,
                ratings_loader=ratings_loader,
            )

        return run_tui_watch(
            log_path=log_path,
            card_database=database,
            app_dir=args.app_dir,
            poll_interval=args.poll_interval,
            once=args.once,
            startup_scan=args.startup_scan,
            ratings_loader=ratings_loader,
        )
    except KeyboardInterrupt:
        return 130
    except (
        CardDatabaseError,
        DeckBuilderError,
        DraftLogParseError,
        DraftPoolError,
        LogFollowError,
        SeventeenLandsError,
    ) as error:
        print(f"watch failed: {error}", file=sys.stderr)
        return 1


def _load_watch_card_database(*, args: argparse.Namespace) -> CardDatabase:
    """Load watch card metadata, refreshing automatically when missing.
    Users can still pass a local bulk file for deterministic tests.
    """

    if args.bulk_file is not None:
        return build_card_database_from_bulk_file(path=args.bulk_file)

    return load_or_refresh_card_database(app_dir=args.app_dir)


def handle_replay(args: argparse.Namespace) -> int:
    """Handle deterministic offline replay.
    Card metadata is loaded only from cache or an explicitly supplied bulk file.
    """

    try:
        database = _load_replay_card_database(args=args)
        output = replay_log_file(
            logfile=args.logfile,
            card_database=database,
            ratings_loader=_metadata_augmenting_ratings_loader(
                args=args,
                database=database,
                load_ratings=lambda set_code: load_cached_17lands_data(
                    set_code=set_code,
                    app_dir=args.app_dir,
                ),
            ),
        )
    except (
        CardDatabaseError,
        DeckBuilderError,
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
    """Handle deck-builder pair, spells, lands, and bench output.
    The command is fully offline, using persisted pools and local caches.
    """

    try:
        database = _load_build_card_database(args=args)
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
        _augment_card_database_from_ratings(
            args=args,
            database=database,
            set_code=pool.set_code,
            ratings_data=ratings_data,
        )
        selection, build_sheet = build_deck_from_pool(
            pool=pool,
            card_database=database,
            ratings_data=ratings_data,
            forced_pair=args.pair,
            allow_splash=args.allow_splash,
        )
    except (CardDatabaseError, DeckBuilderError, DraftPoolError, SeventeenLandsError) as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1

    print(
        format_build_result(
            pool=pool,
            selection=selection,
            spell_selection=build_sheet.spell_selection,
            mana_base=build_sheet.mana_base,
        ),
        end="",
    )
    return 0


def _load_build_card_database(*, args: argparse.Namespace) -> CardDatabase:
    """Load build card metadata, refreshing automatically when missing.
    Build is user-facing, so it should not require a separate bootstrap command.
    """

    if args.bulk_file is not None:
        return build_card_database_from_bulk_file(path=args.bulk_file)

    return load_or_refresh_card_database(app_dir=args.app_dir)


def handle_backtest(args: argparse.Namespace) -> int:
    """Handle persisted draft recommendation backtests.
    Missing 17Lands cache falls back to neutral-prior pick scoring.
    """

    try:
        state = load_persisted_backtest_state(
            app_dir=args.app_dir,
            account_id=args.account,
            draft_id=args.draft_id,
        )
        database = _load_backtest_card_database(args=args)
        ratings_data = _load_optional_backtest_ratings_data(
            args=args,
            database=database,
            set_code=state.set_code,
        )
        report = generate_backtest_report(
            state=state,
            card_database=database,
            ratings_data=ratings_data,
            ranking_mode=args.ranking,
        )
    except (BacktestError, CardDatabaseError, DraftPoolError) as error:
        print(f"backtest failed: {error}", file=sys.stderr)
        return 1

    print(format_backtest_report(report), end="")
    return 0


def _load_backtest_card_database(*, args: argparse.Namespace) -> CardDatabase:
    """Load card metadata for backtest reports.
    The command is user-facing, so cached metadata may refresh automatically.
    """

    if args.bulk_file is not None:
        return build_card_database_from_bulk_file(path=args.bulk_file)

    return load_or_refresh_card_database(app_dir=args.app_dir)


def _load_optional_backtest_ratings_data(
    *,
    args: argparse.Namespace,
    database: CardDatabase,
    set_code: str,
) -> SeventeenLandsData | None:
    """Load cached 17Lands ratings when available for backtests.
    Backtests remain useful with neutral-prior scores if cache is absent.
    """

    try:
        ratings_data = load_cached_17lands_data(
            set_code=set_code,
            app_dir=args.app_dir,
        )
    except SeventeenLandsError:
        return None

    _augment_card_database_from_ratings(
        args=args,
        database=database,
        set_code=set_code,
        ratings_data=ratings_data,
    )
    return ratings_data


def _metadata_augmenting_ratings_loader(
    *,
    args: argparse.Namespace,
    database: CardDatabase,
    load_ratings: Callable[[str], SeventeenLandsData],
) -> Callable[[str], SeventeenLandsData]:
    def load_and_augment(set_code: str) -> SeventeenLandsData:
        ratings_data = load_ratings(set_code)
        _augment_card_database_from_ratings(
            args=args,
            database=database,
            set_code=set_code,
            ratings_data=ratings_data,
        )
        return ratings_data

    return load_and_augment


def _augment_card_database_from_ratings(
    *,
    args: argparse.Namespace,
    database: CardDatabase,
    set_code: str,
    ratings_data: SeventeenLandsData,
) -> None:
    seeds = _metadata_seeds_from_ratings(ratings=ratings_data.ratings.values())
    if not seeds:
        return

    missing_grp_ids = database.unresolved_grp_ids(
        grp_ids=tuple(seed.grp_id for seed in seeds),
    )
    if not missing_grp_ids:
        return

    try:
        augmented = augment_card_database_with_mtgjson_set(
            database,
            set_code=set_code,
            seeds=seeds,
        )
    except CardDatabaseError:
        return

    database.cards.clear()
    database.cards.update(augmented.cards)
    if getattr(args, "bulk_file", None) is not None:
        return

    try:
        save_card_database(database, app_dir=args.app_dir)
    except OSError:
        return


def _metadata_seeds_from_ratings(
    *,
    ratings: Iterable[ResolvedCardRating],
) -> tuple[CardMetadataSeed, ...]:
    seeds: dict[int, CardMetadataSeed] = {}
    for rating in ratings:
        if rating.name.startswith("Unknown card "):
            continue

        seeds[rating.grp_id] = CardMetadataSeed(
            grp_id=rating.grp_id,
            name=rating.name,
            colors=_rating_colors(color=rating.color),
            rarity=rating.rarity or "unknown",
        )

    return tuple(seeds.values())


def _rating_colors(*, color: str | None) -> tuple[str, ...]:
    if color is None:
        return ()

    return tuple(symbol for symbol in "WUBRG" if symbol in color)



def handle_refresh_data(args: argparse.Namespace) -> int:
    """Handle the refresh-data command.
    Build the local Scryfall/Arena-backed grpId metadata cache.
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


def handle_refresh_structure_targets(args: argparse.Namespace) -> int:
    """Handle empirical structure-target computation.
    Uses 17Lands public draft data dumps rather than scraping trophy pages.
    """

    try:
        database = _load_build_card_database(args=args)
        targets = refresh_17lands_structure_targets(
            set_code=args.set_code,
            event_format=args.format,
            card_database=database,
            app_dir=args.app_dir,
            draft_data_file=args.draft_data_file,
            draft_data_url=args.draft_data_url,
        )
    except (CardDatabaseError, SeventeenLandsError) as error:
        print(f"refresh-structure-targets failed: {error}", file=sys.stderr)
        return 1

    cache_path = seventeen_lands_structure_targets_cache_path(
        set_code=targets.set_code,
        event_format=targets.event_format,
        app_dir=args.app_dir,
    )
    print(
        "refreshed "
        f"{len(targets.targets)} pair structure targets "
        f"from {targets.total_decks} trophy decks at {cache_path}."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Draftgoblin CLI.
    Return a process-style exit code for tests and console-script use.
    """

    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(args=raw_args)

    if args.version:
        print(format_version())
        return 0

    handler: CommandHandler | None = getattr(args, "handler", None)
    if handler is None:
        default_args = parser.parse_args(args=["watch"])
        return default_args.handler(default_args)

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
