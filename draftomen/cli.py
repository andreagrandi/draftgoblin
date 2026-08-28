"""Command-line interface for Draftomen.
Define parser wiring and command handlers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from draftomen import DISCLAIMER, __version__
from draftomen.audit import DraftAuditError
from draftomen.backtest import (
    BacktestError,
    format_backtest_report,
    generate_backtest_report,
    load_persisted_backtest_state,
)
from draftomen.benchmark import (
    PickBenchmarkError,
    format_pick_benchmark_report,
    generate_pick_benchmark_report,
)
from draftomen.carddb import (
    CardDatabase,
    CardDatabaseError,
    build_card_database_from_bulk_file,
    card_database_cache_path,
    load_card_database,
    load_or_refresh_card_database,
    refresh_card_database,
)
from draftomen.corpus import (
    CorpusError,
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_CACHE_DIR,
    SelectionSpec,
    build_corpus,
    build_default_source_specs,
    load_source_config,
)
from draftomen.config import COLOR_PAIRS
from draftomen.deckbuilder import (
    DeckBuilderError,
    build_deck_from_pool,
    format_build_result,
    load_persisted_pool,
    load_pool_file,
)
from draftomen.events import DraftLogParseError
from draftomen.logfollow import LogFollowError
from draftomen.paths import UnsupportedPlatformError, resolve_player_log_path
from draftomen.pool import DraftPoolError
from draftomen.ranking import DEFAULT_RANKING_MODE, RANKING_MODES
from draftomen.replay import ReplayError, replay_log_file
from draftomen.seventeen import (
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    DownloadProgressCallback,
    SeventeenLandsData,
    augment_card_database_from_ratings,
    SeventeenLandsError,
    has_cached_17lands_data,
    load_cached_17lands_data,
    load_or_refresh_17lands_data,
    metadata_augmenting_ratings_loader,
    metadata_augmenting_ratings_progress_loader,
    refresh_17lands_structure_targets,
    seventeen_lands_structure_targets_cache_path,
)
from draftomen.tui import run_tui_watch
from draftomen.watch import run_plain_watch

CommandHandler = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.
    Keep subcommand registration centralized for CLI tests.
    """

    parser = argparse.ArgumentParser(
        prog="draftomen-tui",
        description="Unofficial Quick Draft assistant for MTG Arena (TUI).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the Draft Omen version and required Fan Content disclaimer.",
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
        "--mana-icons",
        action="store_true",
        help="Opt in to Mana font icons in the Textual TUI.",
    )
    splash_group = watch_parser.add_mutually_exclusive_group()
    splash_group.add_argument(
        "--splash",
        dest="splash_enabled",
        action="store_true",
        help="Enable splash recommendations for this watch session.",
    )
    splash_group.add_argument(
        "--no-splash",
        dest="splash_enabled",
        action="store_false",
        help="Disable splash recommendations for this watch session.",
    )
    watch_parser.set_defaults(splash_enabled=None)
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
    replay_parser.add_argument(
        "--no-splash",
        dest="splash_enabled",
        action="store_false",
        help="Disable splash recommendations and splash deck building.",
    )
    replay_parser.set_defaults(splash_enabled=True)
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
        dest="allow_splash",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    build_parser_command.add_argument(
        "--no-splash",
        dest="allow_splash",
        action="store_false",
        help="Build a strict two-color deck without third-color splash cards.",
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
    build_parser_command.set_defaults(allow_splash=True, handler=handle_build)

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
        help="Ranking used for recommendations (default: DO Score).",
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
    backtest_parser.add_argument(
        "--no-splash",
        dest="splash_enabled",
        action="store_false",
        help="Evaluate saved picks with splash recommendations disabled.",
    )
    backtest_parser.set_defaults(splash_enabled=True)
    backtest_parser.set_defaults(handler=handle_backtest)

    benchmark_parser = subparsers.add_parser(
        name="benchmark-picks",
        help="Benchmark pick rankings against 17Lands public trophy drafts.",
        description=(
            "Offline benchmark for 17Lands WR vs DO Score using a local "
            "17Lands public draft-data CSV(.gz) dump."
        ),
    )
    benchmark_parser.add_argument(
        "--set-code",
        required=True,
        help="Set code for the public draft data dump.",
    )
    benchmark_parser.add_argument(
        "--format",
        default=PREMIER_DRAFT_FORMAT,
        help=f"17Lands event format (default: {PREMIER_DRAFT_FORMAT}).",
    )
    benchmark_parser.add_argument(
        "--draft-data-file",
        type=Path,
        required=True,
        help="Local 17Lands public draft-data CSV(.gz) or tar.gz file to analyze.",
    )
    benchmark_parser.add_argument(
        "--max-drafts",
        type=int,
        default=None,
        help="Optional cap on matching trophy drafts for quick smoke runs.",
    )
    benchmark_parser.set_defaults(trophy_only=True)
    benchmark_parser.add_argument(
        "--trophy-only",
        dest="trophy_only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    benchmark_parser.add_argument(
        "--include-non-trophy",
        dest="trophy_only",
        action="store_false",
        help="Benchmark all matching drafts instead of only trophy drafts.",
    )
    benchmark_parser.add_argument(
        "--refresh-ratings",
        action="store_true",
        help="Refresh 17Lands ratings before running instead of using cache only.",
    )
    benchmark_parser.add_argument(
        "--bulk-file",
        type=Path,
        default=None,
        help=(
            "Resolve card names from a local Scryfall JSONL(.gz) bulk file "
            "instead of the cached card database."
        ),
    )
    benchmark_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    benchmark_parser.set_defaults(handler=handle_benchmark_picks)

    refresh_parser = subparsers.add_parser(
        name="refresh-data",
        help="Refresh cached Scryfall card metadata.",
        description=(
            "Refresh the local Scryfall grpId card metadata cache, overlaying "
            "MTG Arena local data when available. The command fails if Scryfall "
            "cannot produce a cacheable result."
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
    corpus_parser = subparsers.add_parser(
        name="corpus-build",
        help="Build the development-only semantic analysis card corpus.",
        description=(
            "Acquire pinned Scryfall/Arena/MTGJSON inputs and emit deterministic "
            "offline normalized rows and coverage reports."
        ),
    )
    corpus_parser.add_argument(
        "--source-spec",
        type=Path,
        default=Path("draftomen/corpus_sources.json"),
        help="JSON source and selection configuration (default: draftomen/corpus_sources.json).",
    )
    corpus_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Development cache for source bytes and lock metadata.",
    )
    corpus_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for deterministic normalized.jsonl and coverage.json artifacts.",
    )
    corpus_parser.add_argument(
        "--arena-data-dir",
        type=Path,
        default=None,
        help="Local MTG Arena Data directory containing data_cards* and data_loc* files.",
    )
    corpus_parser.add_argument(
        "--scryfall-file",
        type=Path,
        default=None,
        help="Use a local Scryfall JSONL(.gz) fixture instead of the configured URL.",
    )
    corpus_parser.add_argument(
        "--mtgjson-file",
        type=Path,
        action="append",
        default=[],
        help="Add a local MTGJSON set JSON file (repeatable).",
    )
    corpus_parser.add_argument(
        "--mtgjson-set",
        action="append",
        default=[],
        help="Add a remote MTGJSON set code (repeatable).",
    )
    corpus_parser.add_argument(
        "--set-code",
        action="append",
        default=[],
        help="Select a set explicitly; repeat for multiple sets.",
    )
    corpus_parser.add_argument(
        "--selection",
        choices=("broad", "explicit"),
        default=None,
        help="Selection policy override (default: value in source spec).",
    )
    corpus_parser.add_argument(
        "--offline",
        action="store_true",
        help="Use only locked, checksum-verified source bytes.",
    )
    corpus_parser.add_argument("--timeout", type=int, default=60)
    corpus_parser.set_defaults(handler=handle_corpus_build)

    structure_parser.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    structure_parser.set_defaults(handler=handle_refresh_structure_targets)

    return parser


def format_version() -> str:
    """Format the TUI version banner.
    Include the required Fan Content and 17Lands disclaimer block.
    """

    return f"draftomen-tui {__version__}\n\n{DISCLAIMER}"


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
        if args.plain:
            database = _load_watch_card_database(args=args)
            ratings_loader = metadata_augmenting_ratings_loader(
                database=database,
                load_ratings=lambda set_code: load_or_refresh_17lands_data(
                    set_code=set_code,
                    app_dir=args.app_dir,
                ),
                app_dir=args.app_dir,
                persist_database=args.bulk_file is None,
            )
            return run_plain_watch(
                log_path=log_path,
                card_database=database,
                app_dir=args.app_dir,
                poll_interval=args.poll_interval,
                once=args.once,
                startup_scan=args.startup_scan,
                ratings_loader=ratings_loader,
                splash_enabled=(
                    True if args.splash_enabled is None else args.splash_enabled
                ),
            )

        def load_ratings(
            set_code: str,
            progress_callback: DownloadProgressCallback,
            *,
            refresh: bool,
        ) -> SeventeenLandsData:
            return load_or_refresh_17lands_data(
                set_code=set_code,
                app_dir=args.app_dir,
                refresh=refresh,
                progress_callback=progress_callback,
            )

        return run_tui_watch(
            log_path=log_path,
            card_database_loader=lambda: _load_watch_card_database(args=args),
            app_dir=args.app_dir,
            poll_interval=args.poll_interval,
            once=args.once,
            startup_scan=args.startup_scan,
            ratings_progress_loader_factory=lambda database: (
                metadata_augmenting_ratings_progress_loader(
                    database=database,
                    load_ratings=load_ratings,
                    app_dir=args.app_dir,
                    persist_database=args.bulk_file is None,
                )
            ),
            ratings_cache_checker=lambda set_code: has_cached_17lands_data(
                set_code=set_code,
                app_dir=args.app_dir,
            ),
            mana_icons_enabled=args.mana_icons,
            splash_enabled=args.splash_enabled,
        )
    except KeyboardInterrupt:
        return 130
    except (
        CardDatabaseError,
        DeckBuilderError,
        DraftAuditError,
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
            ratings_loader=lambda set_code: load_cached_17lands_data(
                set_code=set_code,
                app_dir=args.app_dir,
            ),
            splash_enabled=args.splash_enabled,
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
        augment_card_database_from_ratings(
            database=database,
            set_code=pool.set_code,
            ratings_data=ratings_data,
            app_dir=args.app_dir,
            persist_database=args.bulk_file is None,
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
            splash_enabled=args.splash_enabled,
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

    augment_card_database_from_ratings(
        database=database,
        set_code=set_code,
        ratings_data=ratings_data,
        app_dir=args.app_dir,
        persist_database=getattr(args, "bulk_file", None) is None,
    )
    return ratings_data


def handle_benchmark_picks(args: argparse.Namespace) -> int:
    """Handle public-data recommendation calibration benchmarks.
    The benchmark reads local draft data and cached ratings by default.
    """

    try:
        _print_benchmark_progress("loading 17Lands ratings")
        ratings_data = _load_benchmark_ratings_data(args=args)
        _print_benchmark_progress("loading optional cached card metadata")
        database = _load_benchmark_card_database(args=args)
        _print_benchmark_progress(
            "scoring public draft rows; full 17Lands dumps can take a few minutes"
        )
        report = generate_pick_benchmark_report(
            set_code=args.set_code,
            event_format=args.format,
            draft_data_file=args.draft_data_file,
            card_database=database,
            ratings_data=ratings_data,
            max_drafts=args.max_drafts,
            trophy_only=args.trophy_only,
        )
    except (
        CardDatabaseError,
        OSError,
        PickBenchmarkError,
        SeventeenLandsError,
    ) as error:
        print(f"benchmark-picks failed: {error}", file=sys.stderr)
        return 1

    _print_benchmark_progress("done")
    print(format_pick_benchmark_report(report), end="")
    return 0


def _print_benchmark_progress(message: str) -> None:
    print(f"benchmark-picks: {message}...", file=sys.stderr, flush=True)


def _load_benchmark_card_database(*, args: argparse.Namespace) -> CardDatabase:
    """Load optional card metadata without starting large downloads.
    17Lands ratings provide the names/colors needed by the benchmark.
    """

    if args.bulk_file is not None:
        return build_card_database_from_bulk_file(path=args.bulk_file)

    try:
        return load_card_database(app_dir=args.app_dir)
    except CardDatabaseError:
        return CardDatabase(cards={})


def _load_benchmark_ratings_data(*, args: argparse.Namespace) -> SeventeenLandsData:
    """Load 17Lands ratings for the benchmarked set and format.
    Cached data keeps the default path offline; a flag refreshes explicitly.
    """

    if args.refresh_ratings:
        ratings_data = load_or_refresh_17lands_data(
            set_code=args.set_code,
            event_format=args.format,
            app_dir=args.app_dir,
            refresh=True,
        )
    else:
        ratings_data = load_cached_17lands_data(
            set_code=args.set_code,
            event_format=args.format,
            app_dir=args.app_dir,
        )

    if not ratings_data.ratings:
        raise PickBenchmarkError(
            "No cached 17Lands ratings found for this set/format; "
            "rerun with --refresh-ratings."
        )

    return ratings_data

def handle_corpus_build(args: argparse.Namespace) -> int:
    """Build deterministic development-only corpus artifacts."""

    try:
        configured_specs, configured_selection = load_source_config(args.source_spec)
        specs = list(configured_specs)
        if args.scryfall_file is not None:
            specs = [spec for spec in specs if spec.kind != "scryfall"]
            specs.extend(
                build_default_source_specs(scryfall_file=args.scryfall_file)
            )
        if args.arena_data_dir is not None:
            specs.extend(
                spec
                for spec in build_default_source_specs(arena_data_dir=args.arena_data_dir)
                if spec.kind == "arena"
            )
        if args.mtgjson_file:
            specs.extend(
                spec
                for spec in build_default_source_specs(
                    mtgjson_files=args.mtgjson_file
                )
                if spec.kind == "mtgjson"
            )
        if args.mtgjson_set:
            specs.extend(
                spec
                for spec in build_default_source_specs(set_codes=args.mtgjson_set)
                if spec.kind == "mtgjson"
            )
        unique_specs = tuple({spec.name: spec for spec in specs}.values())
        if args.set_code:
            selection = SelectionSpec(mode="explicit", sets=tuple(args.set_code))
        elif args.selection is not None:
            selection = SelectionSpec(
                mode=args.selection,
                sets=configured_selection.sets if args.selection == "explicit" else (),
            )
        else:
            selection = configured_selection
        normalized, report, acquisition, selected = build_corpus(
            source_specs=unique_specs,
            cache_dir=args.cache_dir or DEFAULT_CACHE_DIR,
            output_dir=args.output_dir or DEFAULT_ARTIFACT_DIR,
            selection=selection,
            offline=args.offline,
            timeout_seconds=args.timeout,
        )
    except CorpusError as error:
        print(f"corpus-build failed: {error}", file=sys.stderr)
        return 1

    print(f"built {len(selected)} normalized cards at {normalized}.")
    print(f"coverage report at {report}; source lock at {acquisition.lock_path}.")
    return 0


def handle_refresh_data(args: argparse.Namespace) -> int:
    """Build the local Scryfall-backed grpId metadata cache."""

    try:
        database = refresh_card_database(
            app_dir=args.app_dir,
            bulk_file=args.bulk_file,
            allow_arena_fallback=False,
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
    """Run the Draftomen CLI.
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
