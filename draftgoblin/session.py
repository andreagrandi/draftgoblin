"""Define immutable state and explicit commands for live Draftgoblin sessions.
Frontend adapters consume this contract without importing presentation frameworks.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from os import PathLike
from pathlib import Path
from threading import RLock
from typing import TypeAlias

from draftgoblin.audit import DraftAuditStore
from draftgoblin.backtest import (
    BacktestReport as DomainBacktestReport,
    generate_backtest_report,
    load_persisted_backtest_state,
)
from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import POLL_INTERVAL_SECONDS, SPLASH
from draftgoblin.deckbuilder import (
    BuildPool,
    DeckBuilderError,
    ManaBase,
    PairSelection,
    SpellSelection,
    build_deck_from_pool,
)
from draftgoblin.events import (
    EXPECTED_PICKS_PER_PACK,
    AccountEvent,
    DraftCompletedEvent,
    DraftEvent,
    DraftLogParser,
    DraftStartedEvent,
    PackOfferedEvent,
    PickMadeEvent,
    QuickDraftDetectedEvent,
)
from draftgoblin.logfollow import LogFollower
from draftgoblin.pool import (
    AccountProfile,
    DraftPoolError,
    DraftPoolStore,
    DraftState,
    list_account_profiles,
    list_draft_states,
)
from draftgoblin.pickengine import PickEngine, ScoredCard, ScoredPack
from draftgoblin.ranking import (
    DEFAULT_RANKING_MODE,
    RANKING_MODES,
    RankingMode,
    rank_scored_cards,
    validate_ranking_mode,
)
from draftgoblin.seventeen import (
    DownloadProgressCallback,
    SeventeenLandsData,
    SeventeenLandsDownloadProgress,
)

PathInput: TypeAlias = str | PathLike[str]
SnapshotPublisher: TypeAlias = Callable[["LiveSessionSnapshot"], None]
EventPublisher: TypeAlias = Callable[["LiveSessionEvent"], None]
CardDatabaseLoader: TypeAlias = Callable[[], CardDatabase]
RatingsLoader: TypeAlias = Callable[[str], SeventeenLandsData]
RatingsLoaderFactory: TypeAlias = Callable[[CardDatabase], RatingsLoader]
RatingsProgressLoader: TypeAlias = Callable[
    [str, DownloadProgressCallback],
    SeventeenLandsData,
]
RatingsProgressLoaderFactory: TypeAlias = Callable[
    [CardDatabase],
    RatingsProgressLoader,
]
RatingsCacheChecker: TypeAlias = Callable[[str], bool]


class ApplicationPhase(StrEnum):
    """Identify the user-visible phase of the live application.
    Detailed failures remain separate so phases stay stable across frontends.
    """

    STARTING = "starting"
    WAITING_FOR_DRAFT = "waiting_for_draft"
    DRAFTING = "drafting"
    DRAFT_COMPLETE = "draft_complete"
    STOPPED = "stopped"


class OperationKind(StrEnum):
    """Identify long-running work reported through session progress.
    Frontends decide how each operation is scheduled and presented.
    """

    CARD_DATA = "card_data"
    RATINGS = "ratings"
    BUILD = "build"
    BACKTEST = "backtest"


class DataLoadPhase(StrEnum):
    """Identify reusable data readiness without presentation-specific state.
    Idle resources are configured but have not started loading yet.
    """

    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    MISSING = "missing"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ApplicationStatus:
    """Describe the current application phase and concise status message.
    This state contains no frontend-specific formatting or widget state.
    """

    phase: ApplicationPhase = ApplicationPhase.STARTING
    message: str = "Starting Draftgoblin."


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    """Identify one known Arena account using stable and display values.
    The account id remains authoritative when the screen name is unavailable.
    """

    account_id: str
    screen_name: str | None


@dataclass(frozen=True, slots=True)
class DraftIdentity:
    """Identify the active account-scoped draft and current pick position.
    Optional coordinates represent pre-draft and recovered draft states.
    """

    account_id: str
    draft_id: str
    event_name: str
    set_code: str
    course_id: str | None
    pack_number: int | None
    pick_number: int | None
    completed: bool


@dataclass(frozen=True, slots=True)
class CardView:
    """Provide framework-neutral card facts shared by visual surfaces.
    Cached image paths keep retrieval and filesystem work in Python services.
    """

    grp_id: int
    name: str
    colors: tuple[str, ...]
    rarity: str
    types: tuple[str, ...]
    mana_cost: str | None
    mana_value: float | None
    image_path: str | None


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Describe one ranked card recommendation and its scoring evidence.
    Primitive values let every frontend choose its own presentation.
    """

    rank: int
    card: CardView
    score: int
    win_rate: float | None
    average_last_seen_at: float | None
    source_label: str
    color_fit: str
    no_data: bool


@dataclass(frozen=True, slots=True)
class RecommendationState:
    """Hold the ordered recommendation rows for the current offered pack.
    Selection is state published by Python rather than QML mutation.
    """

    ranking_mode: RankingMode = "score"
    supported_ranking_modes: tuple[RankingMode, ...] = RANKING_MODES
    splash_enabled: bool = SPLASH.enabled_by_default
    cards: tuple[Recommendation, ...] = ()
    selected_grp_id: int | None = None
    source_summary: str | None = None


@dataclass(frozen=True, slots=True)
class CardDataState:
    """Describe shared card metadata readiness for scoring operations.
    Frontends schedule configured loading work and render this primitive state.
    """

    phase: DataLoadPhase = DataLoadPhase.UNAVAILABLE
    message: str = "Card metadata is not configured."


@dataclass(frozen=True, slots=True)
class RatingsState:
    """Describe ratings readiness for the active set and offered pack.
    Rated counts are populated after the current pack has been scored.
    """

    set_code: str | None = None
    phase: DataLoadPhase = DataLoadPhase.UNAVAILABLE
    message: str = "17Lands ratings are not configured."
    rated_cards: int | None = None
    total_cards: int | None = None


@dataclass(frozen=True, slots=True)
class PoolCard:
    """Describe one distinct card and quantity in the drafted pool.
    Stable ordering allows frontends to render deterministic summaries.
    """

    card: CardView
    quantity: int


@dataclass(frozen=True, slots=True)
class PoolState:
    """Describe the drafted pool and its current color commitment.
    Aggregate values avoid forcing presentation code to redo domain work.
    """

    cards: tuple[PoolCard, ...] = ()
    total_cards: int = 0
    inferred_pair: str | None = None
    commitment: float = 0.0


@dataclass(frozen=True, slots=True)
class ProgressState:
    """Report determinate or indeterminate progress for one operation.
    Missing counts represent work whose total is not yet known.
    """

    operation: OperationKind
    message: str
    completed: int | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class SessionError:
    """Describe one stable application error and its recovery capability.
    Error identifiers support explicit dismiss and retry commands.
    """

    error_id: str
    code: str
    message: str
    recoverable: bool
    operation: OperationKind | None = None


@dataclass(frozen=True, slots=True)
class BuildPairOption:
    """Describe one scored color-pair option for a deck build.
    The selected pair and automatic pair remain independently visible.
    """

    pair: str
    score: float
    selected: bool
    automatic: bool
    playable_count: int | None = None
    playable_score_sum: float | None = None
    pair_win_rate: float | None = None


@dataclass(frozen=True, slots=True)
class BuildCard:
    """Describe an ordered spell or bench card in a build result.
    Quantities preserve duplicate picks without repeating presentation rows.
    """

    card: CardView
    quantity: int
    score: int | None = None
    win_rate: float | None = None
    average_last_seen_at: float | None = None
    letter_grade: str | None = None
    source_label: str | None = None
    color_fit: str | None = None
    no_data: bool = False


@dataclass(frozen=True, slots=True)
class BuildLand:
    """Describe one basic or drafted nonbasic land in a build result.
    Optional card data distinguishes generated basics from drafted lands.
    """

    name: str
    quantity: int
    source_colors: tuple[str, ...]
    card: CardView | None = None


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Provide structured pair, spell, land, bench, and warning output.
    Frontends render this data without parsing terminal-formatted build text.
    """

    selected_pair: str
    pair_options: tuple[BuildPairOption, ...]
    spells: tuple[BuildCard, ...]
    lands: tuple[BuildLand, ...]
    bench: tuple[BuildCard, ...]
    deck_size: int
    pair_override: str | None = None
    warnings: tuple[str, ...] = ()
    domain_pool: BuildPool | None = None
    domain_selection: PairSelection | None = None
    domain_spell_selection: SpellSelection | None = None
    domain_mana_base: ManaBase | None = None


@dataclass(frozen=True, slots=True)
class BacktestPickResult:
    """Describe the recommendation comparison for one persisted draft pick.
    Missing cards and match state preserve skipped-history outcomes.
    """

    pack_number: int
    pick_number: int
    recommended: CardView | None
    actual: CardView | None
    match: bool | None
    skipped_reason: str | None
    data_source: str | None
    pool_size: int | None = None
    offered_count: int | None = None
    recommended_score: int | None = None
    recommended_win_rate: float | None = None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Provide structured backtest rows and their aggregate outcome counts.
    Data sources remain available for attribution and fallback disclosure.
    """

    ranking_mode: RankingMode
    rows: tuple[BacktestPickResult, ...]
    match_count: int
    compared_count: int
    skipped_count: int
    data_sources: tuple[str, ...]
    account_id: str | None = None
    account_screen_name: str | None = None
    draft_id: str | None = None
    set_code: str | None = None
    event_name: str | None = None
    completed: bool | None = None
    chosen_pick_count: int | None = None


@dataclass(frozen=True, slots=True)
class LiveSessionSnapshot:
    """Publish all UI-neutral state needed by Draftgoblin frontends.
    Replacing snapshots keeps presentation adapters out of domain mutation.
    """

    status: ApplicationStatus = field(default_factory=ApplicationStatus)
    accounts: tuple[AccountIdentity, ...] = ()
    active_account: AccountIdentity | None = None
    draft: DraftIdentity | None = None
    card_data: CardDataState = field(default_factory=CardDataState)
    ratings: RatingsState = field(default_factory=RatingsState)
    recommendations: RecommendationState = field(default_factory=RecommendationState)
    pool: PoolState = field(default_factory=PoolState)
    current_pack_event: PackOfferedEvent | None = None
    current_scored_pack: ScoredPack | None = None
    progress: ProgressState | None = None
    errors: tuple[SessionError, ...] = ()
    build: BuildResult | None = None
    backtest: BacktestResult | None = None


@dataclass(frozen=True, slots=True)
class LiveSessionEvent:
    """Publish one consumed domain event with its resulting session state.
    Event adapters can preserve ordering without duplicating session orchestration.
    """

    event: DraftEvent
    snapshot: LiveSessionSnapshot
    scored_pack: ScoredPack | None = None


@dataclass(frozen=True, slots=True)
class ChooseAccount:
    """Request that the session make one known account active.
    The session remains responsible for resolving its persisted draft.
    """

    account_id: str


@dataclass(frozen=True, slots=True)
class ChooseRecommendation:
    """Request that one recommendation become the selected card.
    Selection changes are published in the next immutable snapshot.
    """

    grp_id: int


@dataclass(frozen=True, slots=True)
class ChangeRanking:
    """Request a supported recommendation and backtest ranking mode.
    The application layer validates and applies the functional choice.
    """

    ranking_mode: RankingMode


@dataclass(frozen=True, slots=True)
class ChangeSplashPreference:
    """Request whether supported splash recommendations are considered.
    The application layer owns rescoring and persistence consequences.
    """

    enabled: bool


@dataclass(frozen=True, slots=True)
class RequestRatingsDownload:
    """Request ratings retrieval for one active draft set.
    Progress and recoverable failures return through session state.
    """

    set_code: str


@dataclass(frozen=True, slots=True)
class RequestBuild:
    """Request a deck build with an optional explicit pair override.
    Structured build output returns through the immutable snapshot.
    """

    pair_override: str | None = None
    allow_splash: bool = True


@dataclass(frozen=True, slots=True)
class RequestBacktest:
    """Request a persisted-draft backtest using explicit identity filters.
    Missing filters allow the application to select the active draft.
    """

    account_id: str | None = None
    draft_id: str | None = None


@dataclass(frozen=True, slots=True)
class DismissError:
    """Request removal of one recoverable or acknowledged error.
    The error identifier avoids direct mutation of an errors collection.
    """

    error_id: str


@dataclass(frozen=True, slots=True)
class RetryError:
    """Request retry of the operation associated with one error.
    The application decides whether the current state permits a retry.
    """

    error_id: str


LiveSessionCommand: TypeAlias = (
    ChooseAccount
    | ChooseRecommendation
    | ChangeRanking
    | ChangeSplashPreference
    | RequestRatingsDownload
    | RequestBuild
    | RequestBacktest
    | DismissError
    | RetryError
)


class LiveSession:
    """Coordinate live Arena ingestion and persisted draft lifecycle state.
    Frontends schedule polling and receive immutable snapshots through one contract.
    """

    def __init__(
        self,
        *,
        log_path: PathInput,
        card_database: CardDatabase | None = None,
        card_database_loader: CardDatabaseLoader | None = None,
        app_dir: PathInput | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        previous_log_path: PathInput | None = None,
        snapshot_publisher: SnapshotPublisher | None = None,
        event_publisher: EventPublisher | None = None,
        ranking_mode: RankingMode = DEFAULT_RANKING_MODE,
        ratings_loader: RatingsLoader | None = None,
        ratings_loader_factory: RatingsLoaderFactory | None = None,
        ratings_progress_loader: RatingsProgressLoader | None = None,
        ratings_progress_loader_factory: RatingsProgressLoaderFactory | None = None,
        ratings_cache_checker: RatingsCacheChecker | None = None,
        lazy_pair_card_ratings: bool = False,
        splash_enabled: bool = SPLASH.enabled_by_default,
    ) -> None:
        if card_database is not None and card_database_loader is not None:
            raise ValueError(
                "card_database and card_database_loader are mutually exclusive."
            )
        configured_ratings_loaders = sum(
            loader is not None
            for loader in (
                ratings_loader,
                ratings_loader_factory,
                ratings_progress_loader,
                ratings_progress_loader_factory,
            )
        )
        if configured_ratings_loaders > 1:
            raise ValueError("Configure exactly one ratings loader or loader factory.")

        self.log_path = Path(log_path).expanduser().resolve(strict=False)
        self.follower = LogFollower(
            log_path=self.log_path,
            app_dir=app_dir,
            poll_interval=poll_interval,
            previous_log_path=previous_log_path,
        )
        self.parser = DraftLogParser()
        self.store = DraftPoolStore(app_dir=app_dir)
        self.audit_store = DraftAuditStore(app_dir=self.store.root.parent)
        self._state_lock = RLock()
        self._snapshot_publisher = snapshot_publisher
        self._event_publisher = event_publisher
        self._ranking_mode = validate_ranking_mode(ranking_mode=ranking_mode)
        self._splash_enabled = splash_enabled
        self._card_database = card_database
        self._card_database_loader = card_database_loader
        self._ratings_loader = ratings_loader
        self._ratings_loader_factory = ratings_loader_factory
        self._ratings_progress_loader = ratings_progress_loader
        self._ratings_progress_loader_factory = ratings_progress_loader_factory
        self._ratings_cache_checker = ratings_cache_checker
        self._lazy_pair_card_ratings = lazy_pair_card_ratings
        self._ratings_data_by_set: dict[str, SeventeenLandsData | None] = {}
        self._ratings_state_by_set: dict[str, RatingsState] = {}
        self._ratings_progress_by_set: dict[str, ProgressState] = {}
        self._ratings_errors_by_set: dict[str, SessionError] = {}
        self._loading_rating_sets: set[str] = set()
        self._active_set_code_value: str | None = None
        self._current_pack_event: PackOfferedEvent | None = None
        self._current_scored_pack: ScoredPack | None = None
        self._transient_pool_grp_ids: tuple[int, ...] = ()
        self._last_build_request: RequestBuild | None = None
        self._last_backtest_request: RequestBacktest | None = None
        self._build_request_generation = 0
        self._backtest_request_generation = 0
        self._login_generation = self.parser.login_generation
        self._log_account_id: str | None = None
        self._states_by_key: dict[tuple[str, str], DraftState] = {}
        self._screen_names_by_account_id: dict[str, str] = {}
        self._configure_ratings_loader_for_card_database()
        if card_database is not None:
            card_data = CardDataState(
                phase=DataLoadPhase.READY,
                message="Card metadata is ready.",
            )
        elif card_database_loader is not None:
            card_data = CardDataState(
                phase=DataLoadPhase.IDLE,
                message="Card metadata is ready to load.",
            )
        else:
            card_data = CardDataState()
        ratings_state = self._initial_ratings_state()
        self._snapshot = LiveSessionSnapshot(
            status=ApplicationStatus(
                phase=ApplicationPhase.WAITING_FOR_DRAFT,
                message="Waiting for a Quick Draft.",
            ),
            accounts=self._known_accounts(),
            card_data=card_data,
            ratings=ratings_state,
            recommendations=RecommendationState(
                ranking_mode=self._ranking_mode,
                splash_enabled=splash_enabled,
            ),
        )

    @property
    def snapshot(self) -> LiveSessionSnapshot:
        """Return the latest immutable application snapshot.
        Reading state has no parsing, persistence, or publication side effects.
        """

        return self._snapshot

    @property
    def card_database(self) -> CardDatabase | None:
        """Return the card database currently owned by the live session.
        Frontends may use it for presentation-only metadata lookups.
        """

        return self._card_database

    @property
    def current_pack_event(self) -> PackOfferedEvent | None:
        """Return the active immutable pack event, when one is available.
        Presentation adapters may use its coordinates and offered card ids.
        """

        return self.snapshot.current_pack_event

    @property
    def current_scored_pack(self) -> ScoredPack | None:
        """Return the active immutable scored pack, when one is available.
        Presentation adapters may retain their existing domain renderers.
        """

        return self.snapshot.current_scored_pack

    def ratings_data(self, *, set_code: str) -> SeventeenLandsData | None:
        """Return loaded ratings for presentation-adjacent legacy services.
        The live session remains the only owner of ratings loading and caching.
        """

        return self._ratings_data_by_set.get(set_code.upper())

    def known_accounts(self) -> tuple[AccountIdentity, ...]:
        """Return known accounts from current profiles and persisted drafts.
        Adapters can refresh choices added after session construction.
        """

        return self._known_accounts()

    def poll_once(self) -> LiveSessionSnapshot:
        """Process one follower polling cycle and return the latest snapshot.
        Frontends decide whether this call runs on a worker or event-loop callback.
        """

        return self.process_lines(lines=self.follower.poll())

    def scan_startup_files(
        self,
        *,
        include_previous: bool = True,
        include_pre_draft_detection: bool = True,
    ) -> LiveSessionSnapshot:
        """Process startup recovery logs in follower-defined chronological order.
        The follower advances its offset so later polling does not replay current lines.
        """

        return self.process_lines(
            lines=self.follower.scan_startup_files(
                include_previous=include_previous,
            ),
            include_pre_draft_detection=include_pre_draft_detection,
        )

    def process_lines(
        self,
        *,
        lines: Iterable[str],
        include_pre_draft_detection: bool = True,
    ) -> LiveSessionSnapshot:
        """Consume complete Arena log lines and publish each resulting state change.
        Parser and account context remain incremental across repeated batches.
        """

        for line in lines:
            events = tuple(self.parser.parse_lines(lines=(line,)))
            self._discard_previous_login_account_context()
            for parsed_event in events:
                if (
                    isinstance(parsed_event, QuickDraftDetectedEvent)
                    and not include_pre_draft_detection
                ):
                    continue

                event = self._event_with_log_account(event=parsed_event)
                state = self._consume_store_event(event=event)
                if state is not None:
                    self._remember_state(state=state)
                    self._log_account_id = state.account_id
                    if _event_is_missing_account(event=event):
                        event = replace(event, account_id=state.account_id)
                self._consume_event(event=event, state=state)
                self._publish_event(event=event)
            self._persist_pending_login_name_for_observed_course()

        return self.snapshot

    def dispatch(self, *, command: LiveSessionCommand) -> LiveSessionSnapshot:
        """Apply one explicit frontend intention and publish the resulting snapshot.
        Blocking service work runs synchronously so frontend adapters own scheduling.
        """

        if isinstance(command, ChooseAccount):
            self._choose_account(account_id=command.account_id)
            return self.snapshot

        if isinstance(command, ChangeRanking):
            self._change_ranking(ranking_mode=command.ranking_mode)
            return self.snapshot

        if isinstance(command, ChooseRecommendation):
            self._choose_recommendation(grp_id=command.grp_id)
            return self.snapshot

        if isinstance(command, ChangeSplashPreference):
            self._change_splash_preference(enabled=command.enabled)
            return self.snapshot

        if isinstance(command, RequestRatingsDownload):
            self._request_ratings_download(set_code=command.set_code)
            return self.snapshot

        if isinstance(command, RequestBuild):
            self._request_build(command=command)
            return self.snapshot

        if isinstance(command, RequestBacktest):
            self._request_backtest(command=command)
            return self.snapshot

        if isinstance(command, DismissError):
            self._dismiss_error(error_id=command.error_id)
            return self.snapshot

        if isinstance(command, RetryError):
            self._retry_error(error_id=command.error_id)
            return self.snapshot

        raise ValueError(f"Live session command is not implemented yet: {command!r}.")

    def _request_build(self, *, command: RequestBuild) -> None:
        with self._state_lock:
            self._last_build_request = command
            self._build_request_generation += 1
            generation = self._build_request_generation
            account = self.snapshot.active_account
            draft = self.snapshot.draft
            pool = self.snapshot.pool
            ratings = self.snapshot.ratings
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    progress=ProgressState(
                        operation=OperationKind.BUILD,
                        message="Building deck",
                    ),
                    errors=self._without_operation_error(
                        operation=OperationKind.BUILD,
                    ),
                )
            )
        try:
            build = self._build_result(command=command)
        except Exception as error:
            session_error = SessionError(
                error_id="build",
                code="build_failed",
                message=f"Deck build failed: {error}",
                recoverable=True,
                operation=OperationKind.BUILD,
            )
            with self._state_lock:
                if not self._build_request_is_current(
                    generation=generation,
                    account=account,
                    draft=draft,
                    pool=pool,
                    ratings=ratings,
                ):
                    return
                self._publish(
                    snapshot=replace(
                        self.snapshot,
                        progress=self._progress_after_operation(
                            operation=OperationKind.BUILD,
                        ),
                        errors=self._with_error(error=session_error),
                        build=None,
                    )
                )
            return

        with self._state_lock:
            if not self._build_request_is_current(
                generation=generation,
                account=account,
                draft=draft,
                pool=pool,
                ratings=ratings,
            ):
                return
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    progress=self._progress_after_operation(
                        operation=OperationKind.BUILD,
                    ),
                    errors=self._without_operation_error(
                        operation=OperationKind.BUILD,
                    ),
                    build=build,
                )
            )

    def _build_request_is_current(
        self,
        *,
        generation: int,
        account: AccountIdentity | None,
        draft: DraftIdentity | None,
        pool: PoolState,
        ratings: RatingsState,
    ) -> bool:
        return (
            generation == self._build_request_generation
            and account == self.snapshot.active_account
            and draft == self.snapshot.draft
            and pool == self.snapshot.pool
            and ratings == self.snapshot.ratings
        )

    def _build_result(self, *, command: RequestBuild) -> BuildResult:
        state = self._active_draft_state()
        if state is None:
            raise DeckBuilderError("Deck build unavailable: no active draft.")
        if self._card_database is None:
            raise DeckBuilderError("Deck build unavailable: card metadata is not ready.")

        pool = BuildPool(
            set_code=state.set_code,
            pool_grp_ids=state.pool_grp_ids,
            source_label="live draft",
            account_id=state.account_id,
            draft_id=state.draft_id,
        )
        selection, build_sheet = build_deck_from_pool(
            pool=pool,
            card_database=self._card_database,
            ratings_data=self._ratings_data_for_scoring(set_code=state.set_code),
            forced_pair=command.pair_override,
            allow_splash=command.allow_splash,
        )
        spell_selection = build_sheet.spell_selection
        mana_base = build_sheet.mana_base
        return BuildResult(
            selected_pair=selection.chosen.pair,
            pair_options=tuple(
                BuildPairOption(
                    pair=score.pair,
                    score=score.blended_score,
                    selected=score.pair == selection.chosen.pair,
                    automatic=score.pair == selection.automatic.pair,
                    playable_count=score.playable_count,
                    playable_score_sum=score.playable_score_sum,
                    pair_win_rate=score.pair_win_rate,
                )
                for score in selection.ranked_scores
            ),
            spells=_build_cards(
                cards=tuple(
                    sorted(
                        spell_selection.spells,
                        key=_build_spell_curve_sort_key,
                    )
                ),
            ),
            lands=(
                tuple(
                    BuildLand(
                        name=land.card.name,
                        quantity=1,
                        source_colors=land.source_colors,
                        card=_card_view(card=land.card),
                    )
                    for land in mana_base.nonbasic_lands
                )
                + tuple(
                    BuildLand(
                        name=land.name,
                        quantity=land.count,
                        source_colors=(land.color,),
                    )
                    for land in mana_base.basic_lands
                )
            ),
            bench=_build_cards(cards=spell_selection.bench),
            deck_size=mana_base.total_cards,
            pair_override=selection.forced_pair,
            warnings=(
                tuple(
                    f"Applied spell-selection relaxation: {relaxation}"
                    for relaxation in spell_selection.applied_relaxations
                )
                + mana_base.caveats
            ),
            domain_pool=pool,
            domain_selection=selection,
            domain_spell_selection=spell_selection,
            domain_mana_base=mana_base,
        )

    def _request_backtest(self, *, command: RequestBacktest) -> None:
        with self._state_lock:
            self._last_backtest_request = command
            self._backtest_request_generation += 1
            generation = self._backtest_request_generation
            account = self.snapshot.active_account
            draft = self.snapshot.draft
            ranking_mode = self._ranking_mode
            splash_enabled = self._splash_enabled
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    progress=ProgressState(
                        operation=OperationKind.BACKTEST,
                        message="Running backtest",
                    ),
                    errors=self._without_operation_error(
                        operation=OperationKind.BACKTEST,
                    ),
                )
            )
        try:
            backtest = self._backtest_result(command=command)
        except Exception as error:
            session_error = SessionError(
                error_id="backtest",
                code="backtest_failed",
                message=f"Backtest failed: {error}",
                recoverable=True,
                operation=OperationKind.BACKTEST,
            )
            with self._state_lock:
                if not self._backtest_request_is_current(
                    generation=generation,
                    account=account,
                    draft=draft,
                    ranking_mode=ranking_mode,
                    splash_enabled=splash_enabled,
                ):
                    return
                self._publish(
                    snapshot=replace(
                        self.snapshot,
                        progress=self._progress_after_operation(
                            operation=OperationKind.BACKTEST,
                        ),
                        errors=self._with_error(error=session_error),
                        backtest=None,
                    )
                )
            return

        with self._state_lock:
            if not self._backtest_request_is_current(
                generation=generation,
                account=account,
                draft=draft,
                ranking_mode=ranking_mode,
                splash_enabled=splash_enabled,
            ):
                return
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    progress=self._progress_after_operation(
                        operation=OperationKind.BACKTEST,
                    ),
                    errors=self._without_operation_error(
                        operation=OperationKind.BACKTEST,
                    ),
                    backtest=backtest,
                )
            )

    def _backtest_request_is_current(
        self,
        *,
        generation: int,
        account: AccountIdentity | None,
        draft: DraftIdentity | None,
        ranking_mode: RankingMode,
        splash_enabled: bool,
    ) -> bool:
        return (
            generation == self._backtest_request_generation
            and account == self.snapshot.active_account
            and draft == self.snapshot.draft
            and ranking_mode == self._ranking_mode
            and splash_enabled == self._splash_enabled
        )

    def _progress_after_operation(
        self,
        *,
        operation: OperationKind,
    ) -> ProgressState | None:
        progress = self.snapshot.progress
        if progress is not None and progress.operation != operation:
            return progress

        return None

    def _retire_derived_operations(self) -> tuple[SessionError, ...]:
        self._build_request_generation += 1
        self._backtest_request_generation += 1
        self._last_build_request = None
        self._last_backtest_request = None
        return tuple(
            error
            for error in self.snapshot.errors
            if error.operation not in {OperationKind.BUILD, OperationKind.BACKTEST}
        )

    def _backtest_result(self, *, command: RequestBacktest) -> BacktestResult:
        if self._card_database is None:
            raise ValueError("Card metadata is not ready.")

        account_id, draft_id = self._backtest_identity(command=command)
        state = load_persisted_backtest_state(
            app_dir=self.store.app_dir,
            account_id=account_id,
            draft_id=draft_id,
        )
        report = generate_backtest_report(
            state=state,
            card_database=self._card_database,
            ratings_data=self._ratings_data_for_scoring(set_code=state.set_code),
            ranking_mode=self._ranking_mode,
            splash_enabled=self._splash_enabled,
        )
        return _backtest_result(report=report)

    def _backtest_identity(
        self,
        *,
        command: RequestBacktest,
    ) -> tuple[str | None, str | None]:
        active_draft = self.snapshot.draft
        active_account = self.snapshot.active_account
        account_id = command.account_id
        if account_id is None and active_account is not None:
            account_id = active_account.account_id

        draft_id = command.draft_id
        if (
            draft_id is None
            and active_draft is not None
            and active_draft.account_id == account_id
        ):
            draft_id = active_draft.draft_id

        return account_id, draft_id

    def load_card_data(self) -> LiveSessionSnapshot:
        """Load configured card metadata and publish readiness or failure state.
        Frontends call this synchronous operation from their chosen worker context.
        """

        if self._card_database is not None:
            return self.snapshot
        if self._card_database_loader is None:
            raise ValueError("No card metadata loader is configured.")

        self._publish(
            snapshot=replace(
                self.snapshot,
                card_data=CardDataState(
                    phase=DataLoadPhase.LOADING,
                    message="Loading card metadata.",
                ),
                progress=ProgressState(
                    operation=OperationKind.CARD_DATA,
                    message="Loading card metadata",
                ),
                errors=self._without_operation_error(
                    operation=OperationKind.CARD_DATA,
                ),
            )
        )
        try:
            database = self._card_database_loader()
        except Exception as error:
            self._finish_card_data_load(database=None, error_message=str(error))
        else:
            self._finish_card_data_load(database=database, error_message=None)

        return self.snapshot

    def stop(self) -> LiveSessionSnapshot:
        """Publish the terminal stopped state without owning process shutdown.
        Frontends remain responsible for timers, workers, and event-loop teardown.
        """

        self._publish(
            snapshot=replace(
                self.snapshot,
                status=ApplicationStatus(
                    phase=ApplicationPhase.STOPPED,
                    message="Draftgoblin stopped.",
                ),
            )
        )
        return self.snapshot

    def _initial_ratings_state(self) -> RatingsState:
        if self._ratings_configured():
            return RatingsState(
                phase=DataLoadPhase.IDLE,
                message="17Lands ratings are ready to load.",
            )

        return RatingsState()

    def _ratings_configured(self) -> bool:
        return any(
            loader is not None
            for loader in (
                self._ratings_loader,
                self._ratings_loader_factory,
                self._ratings_progress_loader,
                self._ratings_progress_loader_factory,
            )
        )

    def _configure_ratings_loader_for_card_database(self) -> None:
        database = self._card_database
        if database is None:
            return

        if self._ratings_loader_factory is not None:
            self._ratings_loader = self._ratings_loader_factory(database)
        if self._ratings_progress_loader_factory is not None:
            self._ratings_progress_loader = self._ratings_progress_loader_factory(
                database
            )

    def _finish_card_data_load(
        self,
        *,
        database: CardDatabase | None,
        error_message: str | None,
    ) -> None:
        if database is None:
            detail = error_message or "no card metadata was returned"
            session_error = SessionError(
                error_id="card-data",
                code="card_data_unavailable",
                message=f"Card metadata failed to load: {detail}.",
                recoverable=True,
                operation=OperationKind.CARD_DATA,
            )
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    card_data=CardDataState(
                        phase=DataLoadPhase.FAILED,
                        message=session_error.message,
                    ),
                    progress=None,
                    errors=self._with_error(error=session_error),
                )
            )
            return

        self._card_database = database
        self._configure_ratings_loader_for_card_database()
        self._publish(
            snapshot=replace(
                self.snapshot,
                card_data=CardDataState(
                    phase=DataLoadPhase.READY,
                    message="Card metadata is ready.",
                ),
                progress=None,
                errors=self._without_error_id(error_id="card-data"),
                pool=self._pool_state_from_active_draft(),
            )
        )
        set_code = self._active_set_code()
        if set_code is not None:
            self._ensure_ratings_loaded(set_code=set_code)
        self._score_current_pack()

    def _ensure_ratings_loaded(self, *, set_code: str) -> None:
        normalized_set_code = set_code.upper()
        existing = self._ratings_state_by_set.get(normalized_set_code)
        waiting_for_card_data = (
            existing is not None
            and existing.phase == DataLoadPhase.IDLE
            and self._card_database is None
        )
        if existing is not None and (
            existing.phase != DataLoadPhase.IDLE or waiting_for_card_data
        ):
            self._publish_active_ratings_state(state=existing)
            return

        if not self._ratings_configured():
            state = RatingsState(
                set_code=normalized_set_code,
                phase=DataLoadPhase.UNAVAILABLE,
                message=(
                    f"17Lands ratings are unavailable for {normalized_set_code}; "
                    "neutral-prior scores are active."
                ),
            )
            self._ratings_state_by_set[normalized_set_code] = state
            self._publish_active_ratings_state(state=state)
            return

        if (
            self._card_database is None
            and (
                self._ratings_loader_factory is not None
                or self._ratings_progress_loader_factory is not None
            )
        ):
            state = RatingsState(
                set_code=normalized_set_code,
                phase=DataLoadPhase.IDLE,
                message=(
                    f"17Lands ratings for {normalized_set_code} are waiting for "
                    "card metadata."
                ),
            )
            self._ratings_state_by_set[normalized_set_code] = state
            self._publish_active_ratings_state(state=state)
            return

        if self._ratings_cache_checker is not None:
            try:
                cached = self._ratings_cache_checker(normalized_set_code)
            except Exception as error:
                self._finish_ratings_load(
                    set_code=normalized_set_code,
                    ratings_data=None,
                    error_message=str(error),
                )
                return

            if not cached:
                state = RatingsState(
                    set_code=normalized_set_code,
                    phase=DataLoadPhase.MISSING,
                    message=(
                        f"No local 17Lands data for {normalized_set_code}; "
                        "neutral-prior scores are active."
                    ),
                )
                self._ratings_data_by_set[normalized_set_code] = None
                self._ratings_state_by_set[normalized_set_code] = state
                self._publish_active_ratings_state(state=state)
                return

        self._load_ratings(set_code=normalized_set_code)

    def _request_ratings_download(self, *, set_code: str) -> None:
        normalized_set_code = set_code.upper()
        active_set_code = self._active_set_code()
        if active_set_code is None:
            raise ValueError("Ratings downloads require an active draft set.")
        if normalized_set_code != active_set_code:
            raise ValueError(
                f"Ratings download set {normalized_set_code!r} does not match "
                f"active set {active_set_code!r}."
            )
        if self._ratings_data_by_set.get(normalized_set_code) is not None:
            return

        self._load_ratings(set_code=normalized_set_code)

    def _load_ratings(self, *, set_code: str) -> None:
        if self._ratings_loader is None and self._ratings_progress_loader is None:
            state = RatingsState(
                set_code=set_code,
                phase=DataLoadPhase.UNAVAILABLE,
                message=f"No 17Lands ratings loader is available for {set_code}.",
            )
            self._ratings_state_by_set[set_code] = state
            self._publish_active_ratings_state(state=state)
            return

        if not self._begin_ratings_load(set_code=set_code):
            return

        ratings_data = None
        error_message = None
        try:
            if self._ratings_progress_loader is not None:
                ratings_data = self._ratings_progress_loader(
                    set_code,
                    lambda progress: self._update_ratings_progress(
                        set_code=set_code,
                        progress=progress,
                    ),
                )
            elif self._ratings_loader is not None:
                ratings_data = self._ratings_loader(set_code)
        except Exception as error:
            error_message = str(error)

        self._finish_ratings_load(
            set_code=set_code,
            ratings_data=ratings_data,
            error_message=error_message,
        )

    def _begin_ratings_load(self, *, set_code: str) -> bool:
        loading_state = RatingsState(
            set_code=set_code,
            phase=DataLoadPhase.LOADING,
            message=f"Checking 17Lands data for {set_code}.",
        )
        loading_progress = ProgressState(
            operation=OperationKind.RATINGS,
            message=f"Checking 17Lands data for {set_code}",
            completed=0,
        )
        with self._state_lock:
            if set_code in self._loading_rating_sets:
                return False

            self._loading_rating_sets.add(set_code)
            self._ratings_state_by_set[set_code] = loading_state
            self._ratings_progress_by_set[set_code] = loading_progress
            self._ratings_errors_by_set.pop(set_code, None)
            if self._active_set_code_value == set_code:
                self._publish(
                    snapshot=replace(
                        self.snapshot,
                        ratings=loading_state,
                        progress=loading_progress,
                        errors=self._without_operation_error(
                            operation=OperationKind.RATINGS,
                        ),
                    )
                )
                self._score_current_pack()

        return True

    def _update_ratings_progress(
        self,
        *,
        set_code: str,
        progress: SeventeenLandsDownloadProgress,
    ) -> None:
        with self._state_lock:
            state = self._ratings_state_by_set.get(set_code)
            if state is None or state.phase != DataLoadPhase.LOADING:
                return

            next_state = replace(state, message=progress.message)
            next_progress = ProgressState(
                operation=OperationKind.RATINGS,
                message=progress.message,
                completed=progress.completed_requests,
                total=progress.total_requests,
            )
            self._ratings_state_by_set[set_code] = next_state
            self._ratings_progress_by_set[set_code] = next_progress
            if self._active_set_code_value != set_code:
                return

            self._publish(
                snapshot=replace(
                    self.snapshot,
                    ratings=next_state,
                    progress=next_progress,
                )
            )

    def _finish_ratings_load(
        self,
        *,
        set_code: str,
        ratings_data: SeventeenLandsData | None,
        error_message: str | None,
    ) -> None:
        with self._state_lock:
            self._finish_ratings_load_locked(
                set_code=set_code,
                ratings_data=ratings_data,
                error_message=error_message,
            )

    def _finish_ratings_load_locked(
        self,
        *,
        set_code: str,
        ratings_data: SeventeenLandsData | None,
        error_message: str | None,
    ) -> None:
        self._loading_rating_sets.discard(set_code)
        if ratings_data is None:
            detail = error_message or "no ratings were returned"
            session_error = SessionError(
                error_id=self._ratings_error_id(set_code=set_code),
                code="ratings_unavailable",
                message=f"17Lands ratings failed for {set_code}: {detail}.",
                recoverable=True,
                operation=OperationKind.RATINGS,
            )
            state = RatingsState(
                set_code=set_code,
                phase=DataLoadPhase.FAILED,
                message=(
                    f"{session_error.message} Neutral-prior scores remain active."
                ),
            )
            self._ratings_data_by_set[set_code] = None
            self._ratings_state_by_set[set_code] = state
            self._ratings_progress_by_set.pop(set_code, None)
            self._ratings_errors_by_set[set_code] = session_error
            if self._active_set_code_value != set_code:
                return

            failed_snapshot = replace(
                self.snapshot,
                ratings=state,
                progress=None,
                errors=self._with_error(error=session_error),
            )
            if not self._score_current_pack_locked(snapshot=failed_snapshot):
                self._publish(snapshot=failed_snapshot)
            return

        self._ratings_data_by_set[set_code] = ratings_data
        state = RatingsState(
            set_code=set_code,
            phase=DataLoadPhase.READY,
            message=f"17Lands ratings are ready for {set_code}.",
        )
        self._ratings_state_by_set[set_code] = state
        self._ratings_progress_by_set.pop(set_code, None)
        self._ratings_errors_by_set.pop(set_code, None)
        if self._active_set_code_value != set_code:
            return

        ready_snapshot = replace(
            self.snapshot,
            ratings=state,
            progress=None,
            errors=self._without_error_id(
                error_id=self._ratings_error_id(set_code=set_code),
            ),
        )
        if not self._score_current_pack_locked(snapshot=ready_snapshot):
            self._publish(snapshot=ready_snapshot)

    def _publish_active_ratings_state(self, *, state: RatingsState) -> None:
        with self._state_lock:
            set_code = state.set_code
            if set_code != self._active_set_code_value:
                return
            if (
                set_code is not None
                and self._ratings_state_by_set.get(set_code) != state
            ):
                return

            progress = (
                None
                if set_code is None
                else self._ratings_progress_by_set.get(set_code)
            )
            errors = self._without_operation_error(
                operation=OperationKind.RATINGS
            )
            ratings_error = (
                None
                if set_code is None
                else self._ratings_errors_by_set.get(set_code)
            )
            if ratings_error is not None:
                errors += (ratings_error,)
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    ratings=state,
                    progress=progress,
                    errors=errors,
                )
            )

    def _score_current_pack(self) -> None:
        with self._state_lock:
            self._score_current_pack_locked()

    def _score_current_pack_locked(
        self,
        *,
        snapshot: LiveSessionSnapshot | None = None,
    ) -> bool:
        event = self._current_pack_event
        database = self._card_database
        if (
            event is None
            or database is None
            or event.set_code.upper() != self._active_set_code_value
        ):
            return False

        ratings_data = self._ratings_data_for_scoring(set_code=event.set_code)
        engine = PickEngine(
            ratings_data=ratings_data,
            splash_enabled=self._splash_enabled,
        )
        scored_pack = engine.score_pack(
            offered_grp_ids=event.offered_grp_ids,
            card_database=database,
            pool_grp_ids=event.pool_grp_ids,
            pick_index=_draft_pick_index(event=event),
        )
        self._current_scored_pack = scored_pack
        recommendations = self._recommendation_state(scored_pack=scored_pack)
        ratings = self._ratings_state_after_scoring(
            set_code=event.set_code,
            recommendations=recommendations,
        )
        state = self._active_draft_state()
        if state is not None:
            self.audit_store.record_decision(
                state=state,
                event=event,
                scored_pack=scored_pack,
                config=engine.config,
                ratings_data=ratings_data,
            )
        self._publish(
            snapshot=replace(
                self.snapshot if snapshot is None else snapshot,
                ratings=ratings,
                recommendations=recommendations,
                pool=self._pool_state(
                    pool_grp_ids=event.pool_grp_ids,
                    scored_pack=scored_pack,
                ),
            )
        )
        return True

    def _recommendation_state(self, *, scored_pack: ScoredPack) -> RecommendationState:
        ranked_cards = rank_scored_cards(
            cards=scored_pack.cards,
            ranking_mode=self._ranking_mode,
        )
        selected_grp_id = self.snapshot.recommendations.selected_grp_id
        available_grp_ids = {card.card.grp_id for card in ranked_cards}
        if selected_grp_id not in available_grp_ids:
            selected_grp_id = None
        return RecommendationState(
            ranking_mode=self._ranking_mode,
            splash_enabled=self._splash_enabled,
            cards=tuple(
                self._recommendation(rank=rank, scored_card=scored_card)
                for rank, scored_card in enumerate(ranked_cards, start=1)
            ),
            selected_grp_id=selected_grp_id,
            source_summary=scored_pack.source_summary,
        )

    def _recommendation(
        self,
        *,
        rank: int,
        scored_card: ScoredCard,
    ) -> Recommendation:
        return Recommendation(
            rank=rank,
            card=_card_view(card=scored_card.card),
            score=scored_card.score,
            win_rate=scored_card.rating.gih_win_rate,
            average_last_seen_at=scored_card.rating.average_last_seen_at,
            source_label=scored_card.source_label,
            color_fit=scored_card.color_fit,
            no_data=scored_card.no_data,
        )

    def _ratings_state_after_scoring(
        self,
        *,
        set_code: str,
        recommendations: RecommendationState,
    ) -> RatingsState:
        normalized_set_code = set_code.upper()
        state = self._ratings_state_by_set.get(
            normalized_set_code,
            RatingsState(set_code=normalized_set_code),
        )
        rated_cards = sum(not card.no_data for card in recommendations.cards)
        next_state = replace(
            state,
            rated_cards=rated_cards,
            total_cards=len(recommendations.cards),
        )
        self._ratings_state_by_set[normalized_set_code] = next_state
        return next_state

    def _ratings_data_for_scoring(self, *, set_code: str) -> SeventeenLandsData | None:
        ratings_data = self._ratings_data_by_set.get(set_code.upper())
        if ratings_data is None:
            return None
        if self._lazy_pair_card_ratings:
            return ratings_data

        return replace(ratings_data, pair_card_ratings_loader=None)

    def _change_ranking(self, *, ranking_mode: str) -> None:
        with self._state_lock:
            self._ranking_mode = validate_ranking_mode(ranking_mode=ranking_mode)
            self._backtest_request_generation += 1
            self._last_backtest_request = None
            if self._current_scored_pack is None:
                recommendations = replace(
                    self.snapshot.recommendations,
                    ranking_mode=self._ranking_mode,
                )
            else:
                recommendations = self._recommendation_state(
                    scored_pack=self._current_scored_pack,
                )
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    recommendations=recommendations,
                    progress=self._progress_after_operation(
                        operation=OperationKind.BACKTEST,
                    ),
                    errors=self._without_operation_error(
                        operation=OperationKind.BACKTEST,
                    ),
                    backtest=None,
                )
            )

    def _choose_recommendation(self, *, grp_id: int) -> None:
        available_grp_ids = {
            recommendation.card.grp_id
            for recommendation in self.snapshot.recommendations.cards
        }
        if grp_id not in available_grp_ids:
            raise ValueError(f"Card {grp_id} is not in the current recommendations.")

        self._publish(
            snapshot=replace(
                self.snapshot,
                recommendations=replace(
                    self.snapshot.recommendations,
                    selected_grp_id=grp_id,
                ),
            )
        )

    def _change_splash_preference(self, *, enabled: bool) -> None:
        with self._state_lock:
            if enabled == self._splash_enabled:
                return

            self._splash_enabled = enabled
            self._build_request_generation += 1
            self._backtest_request_generation += 1
            self._last_build_request = None
            self._last_backtest_request = None
            errors = tuple(
                error
                for error in self.snapshot.errors
                if error.operation
                not in {OperationKind.BUILD, OperationKind.BACKTEST}
            )
            progress = self.snapshot.progress
            if progress is not None and progress.operation in {
                OperationKind.BUILD,
                OperationKind.BACKTEST,
            }:
                progress = None
            if self._current_pack_event is None:
                self._publish(
                    snapshot=replace(
                        self.snapshot,
                        recommendations=replace(
                            self.snapshot.recommendations,
                            splash_enabled=enabled,
                        ),
                        progress=progress,
                        errors=errors,
                        build=None,
                        backtest=None,
                    )
                )
                return

            self._publish(
                snapshot=replace(
                    self.snapshot,
                    progress=progress,
                    errors=errors,
                    build=None,
                    backtest=None,
                )
            )
            self._score_current_pack_locked()

    def _dismiss_error(self, *, error_id: str) -> None:
        if not any(error.error_id == error_id for error in self.snapshot.errors):
            raise ValueError(f"Unknown session error {error_id!r}.")

        self._publish(
            snapshot=replace(
                self.snapshot,
                errors=self._without_error_id(error_id=error_id),
            )
        )

    def _retry_error(self, *, error_id: str) -> None:
        error = next(
            (
                candidate
                for candidate in self.snapshot.errors
                if candidate.error_id == error_id
            ),
            None,
        )
        if error is None:
            raise ValueError(f"Unknown session error {error_id!r}.")
        if not error.recoverable:
            raise ValueError(f"Session error {error_id!r} is not recoverable.")

        if error.operation == OperationKind.CARD_DATA:
            self.load_card_data()
            return
        if error.operation == OperationKind.RATINGS:
            prefix = "ratings:"
            if not error_id.startswith(prefix):
                raise ValueError(f"Ratings error {error_id!r} has no set code.")
            self._request_ratings_download(set_code=error_id.removeprefix(prefix))
            return
        if error.operation == OperationKind.BUILD:
            if self._last_build_request is None:
                raise ValueError(f"Build error {error_id!r} has no saved request.")
            self._request_build(command=self._last_build_request)
            return
        if error.operation == OperationKind.BACKTEST:
            if self._last_backtest_request is None:
                raise ValueError(f"Backtest error {error_id!r} has no saved request.")
            self._request_backtest(command=self._last_backtest_request)
            return

        raise ValueError(f"Session error {error_id!r} has no retry operation.")

    def _with_error(self, *, error: SessionError) -> tuple[SessionError, ...]:
        return self._without_error_id(error_id=error.error_id) + (error,)

    def _without_error_id(self, *, error_id: str) -> tuple[SessionError, ...]:
        return tuple(
            error for error in self.snapshot.errors if error.error_id != error_id
        )

    def _without_operation_error(
        self,
        *,
        operation: OperationKind,
    ) -> tuple[SessionError, ...]:
        return tuple(
            error for error in self.snapshot.errors if error.operation != operation
        )

    def _ratings_error_id(self, *, set_code: str) -> str:
        return f"ratings:{set_code.upper()}"

    def _active_set_code(self) -> str | None:
        with self._state_lock:
            return self._active_set_code_value

    def _set_active_set_code(self, *, set_code: str | None) -> None:
        with self._state_lock:
            self._active_set_code_value = (
                None if set_code is None else set_code.upper()
            )

    def _active_draft_state(self) -> DraftState | None:
        draft = self.snapshot.draft
        if draft is None:
            return None

        return self._states_by_key.get((draft.account_id, draft.draft_id))

    def _pool_state_from_active_draft(self) -> PoolState:
        state = self._active_draft_state()
        if state is None:
            return PoolState()

        return self._pool_state(pool_grp_ids=state.pool_grp_ids)

    def _pool_state(
        self,
        *,
        pool_grp_ids: tuple[int, ...],
        scored_pack: ScoredPack | None = None,
    ) -> PoolState:
        database = self._card_database
        if database is None:
            return PoolState(total_cards=len(pool_grp_ids))

        counts = Counter(pool_grp_ids)
        cards = tuple(
            PoolCard(
                card=_card_view(card=database.lookup(grp_id=grp_id)),
                quantity=counts[grp_id],
            )
            for grp_id in dict.fromkeys(pool_grp_ids)
        )
        return PoolState(
            cards=cards,
            total_cards=len(pool_grp_ids),
            inferred_pair=(
                None
                if scored_pack is None
                else scored_pack.commitment.inferred_pair
            ),
            commitment=(
                0.0 if scored_pack is None else scored_pack.commitment.level
            ),
        )

    def _select_recovered_state(self, *, state: DraftState) -> None:
        self._current_pack_event = _pending_pack_event(state=state)
        self._current_scored_pack = None
        self._select_state(state=state, recovered=True)
        self._ensure_ratings_loaded(set_code=state.set_code)
        self._score_current_pack()

    def _discard_previous_login_account_context(self) -> None:
        if self.parser.login_generation == self._login_generation:
            return

        self._login_generation = self.parser.login_generation
        self._log_account_id = None
        self.store.clear_active_account()
        self._restore_pending_login_account_context()

    def _restore_pending_login_account_context(self) -> None:
        profile = self._pending_login_account_profile()
        if profile is None:
            return

        self._log_account_id = profile.account_id
        self.store.set_active_account(
            account_id=profile.account_id,
            screen_name=profile.screen_name,
        )
        active_account = self.snapshot.active_account
        if active_account is not None and active_account.account_id != profile.account_id:
            return

        if self.snapshot.draft is not None or self.snapshot.pool.total_cards:
            return

        state = self._latest_state_for_account(
            account_id=profile.account_id,
            require_pool=True,
        )
        if state is None:
            self._select_account_without_draft(account_id=profile.account_id)
        else:
            self._select_recovered_state(state=state)

    def _consume_store_event(self, *, event: DraftEvent) -> DraftState | None:
        try:
            return self.store.consume(event=event)
        except DraftPoolError as error:
            if _is_missing_account_error(event=event, error=error):
                return None

            raise

    def _event_with_log_account(self, *, event: DraftEvent) -> DraftEvent:
        if self._log_account_id is None or not _event_is_missing_account(event=event):
            return event

        return replace(event, account_id=self._log_account_id)

    def _consume_event(self, *, event: DraftEvent, state: DraftState | None) -> None:
        if isinstance(event, AccountEvent):
            self._consume_account_event(event=event)
            return

        if isinstance(event, QuickDraftDetectedEvent):
            self._consume_detected_event(event=event)
            return

        if state is None:
            self._consume_accountless_event(event=event)
            return

        if isinstance(event, DraftStartedEvent):
            self._current_pack_event = None
            self._current_scored_pack = None
            self.audit_store.record_draft_started(state=state)
            self._select_state(
                state=state,
                recovered=False,
                event=event,
                message=f"Draft started for {event.set_code}.",
            )
            self._ensure_ratings_loaded(set_code=event.set_code)
            return

        if isinstance(event, PackOfferedEvent):
            self._current_pack_event = event
            self._current_scored_pack = None
            self._select_state(
                state=state,
                recovered=False,
                event=event,
                message=(
                    f"Pack {event.pack_number + 1}, pick {event.pick_number + 1}."
                ),
            )
            self._ensure_ratings_loaded(set_code=event.set_code)
            self._score_current_pack()
            return

        if isinstance(event, PickMadeEvent):
            self.audit_store.record_choice(
                state=state,
                event=event,
                ranking_mode=self._ranking_mode,
            )
            self._select_state(
                state=state,
                recovered=False,
                event=event,
                message=(
                    f"Pack {event.pack_number + 1}, pick "
                    f"{event.pick_number + 1} recorded."
                ),
            )
            return

        if isinstance(event, DraftCompletedEvent):
            self._current_pack_event = None
            self._current_scored_pack = None
            self.audit_store.record_draft_completed(state=state, event=event)
            self._select_state(
                state=state,
                recovered=False,
                event=event,
                message="Draft complete.",
            )
            self._ensure_ratings_loaded(set_code=event.set_code)

    def _consume_accountless_event(self, *, event: DraftEvent) -> None:
        if isinstance(event, DraftStartedEvent):
            self._current_pack_event = None
            self._current_scored_pack = None
            self._transient_pool_grp_ids = ()
            self._set_active_set_code(set_code=event.set_code)
            self._publish_accountless_state(
                event=event,
                phase=ApplicationPhase.WAITING_FOR_DRAFT,
                message="Draft detected; waiting for an Arena account ID.",
                keep_recommendations=False,
            )
            self._ensure_ratings_loaded(set_code=event.set_code)
            return

        if isinstance(event, PackOfferedEvent):
            self._current_pack_event = event
            self._current_scored_pack = None
            self._transient_pool_grp_ids = event.pool_grp_ids
            self._set_active_set_code(set_code=event.set_code)
            self._publish_accountless_state(
                event=event,
                phase=ApplicationPhase.DRAFTING,
                message=(
                    f"Pack {event.pack_number + 1}, pick "
                    f"{event.pick_number + 1}; waiting for an account ID."
                ),
                keep_recommendations=False,
            )
            self._ensure_ratings_loaded(set_code=event.set_code)
            self._score_current_pack()
            return

        if isinstance(event, PickMadeEvent):
            self._transient_pool_grp_ids += (event.chosen_grp_id,)
            self._publish_accountless_state(
                event=event,
                phase=ApplicationPhase.DRAFTING,
                message=(
                    f"Pack {event.pack_number + 1}, pick "
                    f"{event.pick_number + 1} recorded without an account ID."
                ),
                keep_recommendations=True,
            )
            return

        if isinstance(event, DraftCompletedEvent):
            self._current_pack_event = None
            self._current_scored_pack = None
            self._transient_pool_grp_ids = event.picked_grp_ids
            self._set_active_set_code(set_code=event.set_code)
            self._publish_accountless_state(
                event=event,
                phase=ApplicationPhase.DRAFT_COMPLETE,
                message="Draft complete without an account ID.",
                keep_recommendations=False,
            )
            self._ensure_ratings_loaded(set_code=event.set_code)

    def _publish_accountless_state(
        self,
        *,
        event: DraftEvent,
        phase: ApplicationPhase,
        message: str,
        keep_recommendations: bool,
    ) -> None:
        set_code = event.set_code.upper()
        ratings = self._ratings_state_by_set.get(
            set_code,
            replace(self._initial_ratings_state(), set_code=set_code),
        )
        with self._state_lock:
            errors = self._retire_derived_operations()
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    status=ApplicationStatus(phase=phase, message=message),
                    accounts=self._known_accounts(),
                    active_account=None,
                    draft=None,
                    ratings=ratings,
                    recommendations=(
                        self.snapshot.recommendations
                        if keep_recommendations
                        else RecommendationState(
                            ranking_mode=self._ranking_mode,
                            splash_enabled=self._splash_enabled,
                        )
                    ),
                    pool=self._pool_state(
                        pool_grp_ids=self._transient_pool_grp_ids,
                    ),
                    progress=None,
                    errors=errors,
                    build=None,
                    backtest=None,
                )
            )

    def _consume_account_event(self, *, event: AccountEvent) -> None:
        self._log_account_id = event.client_id
        if event.screen_name is not None:
            self._screen_names_by_account_id[event.client_id] = event.screen_name

        active_account = self.snapshot.active_account
        if (
            active_account is not None
            and active_account.account_id != event.client_id
            and (self.snapshot.draft is not None or self.snapshot.pool.total_cards)
        ):
            self._refresh_accounts()
            return

        state = self._latest_state_for_account(
            account_id=event.client_id,
            require_pool=True,
        )
        if state is None:
            self._select_account_without_draft(account_id=event.client_id)
        else:
            self._select_recovered_state(state=state)

    def _consume_detected_event(self, *, event: QuickDraftDetectedEvent) -> None:
        self._current_pack_event = None
        self._current_scored_pack = None
        self._transient_pool_grp_ids = ()
        self._set_active_set_code(set_code=event.set_code)
        account_id = event.account_id or self._log_account_id
        active_account = self._identity_for(account_id=account_id)
        with self._state_lock:
            errors = self._retire_derived_operations()
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    status=ApplicationStatus(
                        phase=ApplicationPhase.WAITING_FOR_DRAFT,
                        message=f"Preparing Quick Draft data for {event.set_code}.",
                    ),
                    accounts=self._known_accounts(),
                    active_account=active_account,
                    draft=None,
                    recommendations=RecommendationState(
                        ranking_mode=self._ranking_mode,
                        splash_enabled=self._splash_enabled,
                    ),
                    pool=PoolState(),
                    progress=None,
                    errors=errors,
                    build=None,
                    backtest=None,
                )
            )
        self._ensure_ratings_loaded(set_code=event.set_code)

    def _choose_account(self, *, account_id: str) -> None:
        known_account_ids = {account.account_id for account in self._known_accounts()}
        if account_id not in known_account_ids:
            raise ValueError(f"Unknown Arena account {account_id!r}.")

        state = self._latest_state_for_account(
            account_id=account_id,
            require_pool=False,
        )
        if state is None:
            self._select_account_without_draft(account_id=account_id)
        else:
            self._select_recovered_state(state=state)

    def _select_account_without_draft(self, *, account_id: str) -> None:
        identity = self._identity_for(account_id=account_id)
        if identity is None:
            raise ValueError(f"Unknown Arena account {account_id!r}.")

        self.store.set_active_account(
            account_id=account_id,
            screen_name=identity.screen_name,
        )
        self._current_pack_event = None
        self._current_scored_pack = None
        self._transient_pool_grp_ids = ()
        self._set_active_set_code(set_code=None)
        with self._state_lock:
            errors = self._retire_derived_operations()
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    status=ApplicationStatus(
                        phase=ApplicationPhase.WAITING_FOR_DRAFT,
                        message="Waiting for a Quick Draft.",
                    ),
                    accounts=self._known_accounts(),
                    active_account=identity,
                    draft=None,
                    ratings=self._initial_ratings_state(),
                    recommendations=RecommendationState(
                        ranking_mode=self._ranking_mode,
                        splash_enabled=self._splash_enabled,
                    ),
                    pool=PoolState(),
                    progress=None,
                    errors=errors,
                    build=None,
                    backtest=None,
                )
            )

    def _select_state(
        self,
        *,
        state: DraftState,
        recovered: bool,
        event: DraftEvent | None = None,
        message: str | None = None,
    ) -> None:
        self._remember_state(state=state)
        self._transient_pool_grp_ids = state.pool_grp_ids
        self._set_active_set_code(set_code=state.set_code)
        self.store.set_active_account(
            account_id=state.account_id,
            screen_name=state.account_screen_name,
        )
        pack_number, pick_number = _draft_coordinates(state=state, event=event)
        if message is None:
            if state.completed:
                message = "Recovered completed draft."
            elif recovered:
                message = "Recovered draft in progress."
            else:
                message = "Draft in progress."
        phase = (
            ApplicationPhase.DRAFT_COMPLETE
            if state.completed
            else ApplicationPhase.DRAFTING
        )
        with self._state_lock:
            errors = self._retire_derived_operations()
            self._publish(
                snapshot=replace(
                    self.snapshot,
                    status=ApplicationStatus(phase=phase, message=message),
                    accounts=self._known_accounts(),
                    active_account=AccountIdentity(
                        account_id=state.account_id,
                        screen_name=self._screen_name_for_state(state=state),
                    ),
                    draft=DraftIdentity(
                        account_id=state.account_id,
                        draft_id=state.draft_id,
                        event_name=state.event_name,
                        set_code=state.set_code,
                        course_id=state.course_id,
                        pack_number=pack_number,
                        pick_number=pick_number,
                        completed=state.completed,
                    ),
                    recommendations=(
                        self.snapshot.recommendations
                        if isinstance(event, PickMadeEvent)
                        else RecommendationState(
                            ranking_mode=self._ranking_mode,
                            splash_enabled=self._splash_enabled,
                        )
                    ),
                    pool=self._pool_state(
                        pool_grp_ids=state.pool_grp_ids,
                    ),
                    progress=None,
                    errors=errors,
                    build=None,
                    backtest=None,
                )
            )

    def _remember_state(self, *, state: DraftState) -> None:
        self._states_by_key[(state.account_id, state.draft_id)] = state
        if state.account_screen_name is not None:
            self._screen_names_by_account_id[state.account_id] = (
                state.account_screen_name
            )

    def _known_accounts(self) -> tuple[AccountIdentity, ...]:
        screen_names: dict[str, str | None] = dict(
            self._screen_names_by_account_id
        )
        for state in self._recovered_states():
            if state.account_screen_name is not None:
                screen_names.setdefault(state.account_id, state.account_screen_name)
            else:
                screen_names.setdefault(state.account_id, None)
        for profile in list_account_profiles(app_dir=self.store.app_dir):
            screen_names[profile.account_id] = profile.screen_name

        return tuple(
            sorted(
                (
                    AccountIdentity(
                        account_id=account_id,
                        screen_name=screen_name,
                    )
                    for account_id, screen_name in screen_names.items()
                ),
                key=lambda account: (
                    (account.screen_name or account.account_id).casefold(),
                    account.account_id,
                ),
            )
        )

    def _identity_for(self, *, account_id: str | None) -> AccountIdentity | None:
        if account_id is None:
            return None

        return next(
            (
                account
                for account in self._known_accounts()
                if account.account_id == account_id
            ),
            AccountIdentity(account_id=account_id, screen_name=None),
        )

    def _refresh_accounts(self) -> None:
        active_account_id = (
            None
            if self.snapshot.active_account is None
            else self.snapshot.active_account.account_id
        )
        self._publish(
            snapshot=replace(
                self.snapshot,
                accounts=self._known_accounts(),
                active_account=self._identity_for(account_id=active_account_id),
            )
        )

    def _pending_login_account_profile(self) -> AccountProfile | None:
        pending_screen_name = self.parser.pending_login_screen_name
        if pending_screen_name is None:
            return None

        pending_key = _account_screen_name_match_key(
            screen_name=pending_screen_name,
        )
        matches = tuple(
            profile
            for profile in list_account_profiles(app_dir=self.store.app_dir)
            if _account_screen_name_match_key(screen_name=profile.screen_name)
            == pending_key
        )
        if len(matches) != 1:
            return None

        return matches[0]

    def _recovered_states(self) -> tuple[DraftState, ...]:
        states = dict(self._states_by_key)
        states.update(
            {
                (state.account_id, state.draft_id): state
                for state in list_draft_states(app_dir=self.store.app_dir)
            }
        )
        return tuple(states.values())

    def _latest_state_for_account(
        self,
        *,
        account_id: str,
        require_pool: bool,
    ) -> DraftState | None:
        states = tuple(
            state
            for state in self._recovered_states()
            if state.account_id == account_id
            and (not require_pool or bool(state.pool_grp_ids))
        )
        if not states:
            return None

        return max(states, key=_latest_draft_state_sort_key)

    def _persist_pending_login_name_for_observed_course(self) -> None:
        screen_name = self.parser.pending_login_screen_name
        course_ids = self.parser.observed_quick_draft_course_ids
        if screen_name is None or not course_ids:
            return

        matching_account_ids = {
            state.account_id
            for state in self._recovered_states()
            if state.course_id in course_ids or state.draft_id in course_ids
        }
        if len(matching_account_ids) != 1:
            return

        account_id = matching_account_ids.pop()
        identity = self._identity_for(account_id=account_id)
        if identity is not None and identity.screen_name is not None:
            return

        self.store.set_active_account(
            account_id=account_id,
            screen_name=screen_name,
        )
        self._screen_names_by_account_id[account_id] = screen_name
        self._refresh_accounts()

    def _screen_name_for_state(self, *, state: DraftState) -> str | None:
        identity = self._identity_for(account_id=state.account_id)
        if identity is not None and identity.screen_name is not None:
            return identity.screen_name

        return state.account_screen_name

    def _publish(self, snapshot: LiveSessionSnapshot) -> None:
        with self._state_lock:
            snapshot = replace(
                snapshot,
                current_pack_event=self._current_pack_event,
                current_scored_pack=self._current_scored_pack,
            )
            if snapshot == self._snapshot:
                return

            self._snapshot = snapshot
            if self._snapshot_publisher is not None:
                self._snapshot_publisher(snapshot)

    def _publish_event(self, *, event: DraftEvent) -> None:
        if self._event_publisher is None:
            return

        scored_pack = (
            self._current_scored_pack
            if isinstance(event, PackOfferedEvent)
            else None
        )
        self._event_publisher(
            LiveSessionEvent(
                event=event,
                snapshot=self.snapshot,
                scored_pack=scored_pack,
            )
        )


def _draft_coordinates(
    *,
    state: DraftState,
    event: DraftEvent | None,
) -> tuple[int | None, int | None]:
    if isinstance(event, (PackOfferedEvent, PickMadeEvent, DraftCompletedEvent)):
        return (event.pack_number, event.pick_number)

    if not state.picks:
        return (None, None)

    pick = max(state.picks, key=lambda candidate: candidate.coordinate)
    return pick.coordinate


def _pending_pack_event(*, state: DraftState) -> PackOfferedEvent | None:
    if state.completed:
        return None

    pending_picks = tuple(
        pick
        for pick in state.picks
        if pick.offered_grp_ids and pick.chosen_grp_id is None
    )
    if not pending_picks:
        return None

    pending_pick = max(pending_picks, key=lambda pick: pick.coordinate)
    offered_grp_ids = pending_pick.offered_grp_ids
    if offered_grp_ids is None:
        return None

    pool_grp_ids = (
        state.pool_grp_ids
        if pending_pick.pool_before_pick is None
        else pending_pick.pool_before_pick
    )
    return PackOfferedEvent(
        event_name=state.event_name,
        set_code=state.set_code,
        pack_number=pending_pick.pack_number,
        pick_number=pending_pick.pick_number,
        offered_grp_ids=offered_grp_ids,
        pool_grp_ids=pool_grp_ids,
        account_id=state.account_id,
    )


def _draft_pick_index(*, event: PackOfferedEvent) -> int:
    return (event.pack_number * EXPECTED_PICKS_PER_PACK) + event.pick_number + 1


def _card_view(*, card: CardInfo) -> CardView:
    return CardView(
        grp_id=card.grp_id,
        name=card.name,
        colors=card.colors,
        rarity=card.rarity,
        types=card.types,
        mana_cost=card.mana_cost,
        mana_value=card.mana_value,
        image_path=None,
    )


def _build_cards(*, cards: tuple[ScoredCard, ...]) -> tuple[BuildCard, ...]:
    grouped: dict[tuple[str, str], BuildCard] = {}
    for scored_card in cards:
        card = scored_card.card
        key = (
            ("unknown", str(card.grp_id))
            if card.unknown
            else ("name", " ".join(card.name.casefold().split()))
        )
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = BuildCard(
                card=_card_view(card=card),
                quantity=1,
                score=scored_card.score,
                win_rate=scored_card.rating.gih_win_rate,
                average_last_seen_at=scored_card.rating.average_last_seen_at,
                letter_grade=scored_card.rating.letter_grade,
                source_label=scored_card.source_label,
                color_fit=scored_card.color_fit,
                no_data=scored_card.no_data,
            )
            continue

        grouped[key] = replace(existing, quantity=existing.quantity + 1)

    return tuple(grouped.values())


def _build_spell_curve_sort_key(card: ScoredCard) -> tuple[float, int, str, int]:
    mana_value = 99.0 if card.card.mana_value is None else card.card.mana_value
    return (mana_value, -card.score, card.card.name, card.original_index)


def _backtest_result(*, report: DomainBacktestReport) -> BacktestResult:
    state = report.state
    return BacktestResult(
        ranking_mode=report.ranking_mode,
        rows=tuple(
            BacktestPickResult(
                pack_number=row.pack_number,
                pick_number=row.pick_number,
                recommended=(
                    None
                    if row.recommended is None
                    else _card_view(card=row.recommended.card)
                ),
                actual=(
                    None if row.actual is None else _card_view(card=row.actual)
                ),
                match=row.match,
                skipped_reason=row.skipped_reason,
                data_source=row.data_source,
                pool_size=row.pool_size,
                offered_count=row.offered_count,
                recommended_score=(
                    None if row.recommended is None else row.recommended.score
                ),
                recommended_win_rate=(
                    None
                    if row.recommended is None
                    else row.recommended.rating.gih_win_rate
                ),
            )
            for row in report.rows
        ),
        match_count=report.match_count,
        compared_count=len(report.compared_rows),
        skipped_count=len(report.skipped_rows),
        data_sources=report.data_sources,
        account_id=state.account_id,
        account_screen_name=state.account_screen_name,
        draft_id=state.draft_id,
        set_code=state.set_code,
        event_name=state.event_name,
        completed=state.completed,
        chosen_pick_count=state.chosen_pick_count,
    )


def _latest_draft_state_sort_key(state: DraftState) -> tuple[str, str, str]:
    return (state.updated_at, state.account_id, state.draft_id)


def _is_missing_account_error(*, event: DraftEvent, error: DraftPoolError) -> bool:
    return (
        str(error) == "Draft event is missing an MTGA account id."
        and _event_is_missing_account(event=event)
    )


def _event_is_missing_account(*, event: DraftEvent) -> bool:
    return (
        isinstance(
            event,
            (
                QuickDraftDetectedEvent,
                DraftStartedEvent,
                PackOfferedEvent,
                PickMadeEvent,
                DraftCompletedEvent,
            ),
        )
        and event.account_id is None
    )


def _account_screen_name_match_key(*, screen_name: str) -> str:
    normalized = screen_name.strip()
    name, separator, discriminator = normalized.rpartition("#")
    if separator and name and discriminator.isdigit():
        normalized = name

    return normalized.casefold()
