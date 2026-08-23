"""Define immutable state and explicit commands for live Draftgoblin sessions.
Frontend adapters consume this contract without importing presentation frameworks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from draftgoblin.ranking import RankingMode


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
    cards: tuple[Recommendation, ...] = ()
    selected_grp_id: int | None = None
    source_summary: str | None = None


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


@dataclass(frozen=True, slots=True)
class BuildCard:
    """Describe an ordered spell or bench card in a build result.
    Quantities preserve duplicate picks without repeating presentation rows.
    """

    card: CardView
    quantity: int


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
    warnings: tuple[str, ...] = ()


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


@dataclass(frozen=True, slots=True)
class LiveSessionSnapshot:
    """Publish all UI-neutral state needed by Draftgoblin frontends.
    Replacing snapshots keeps presentation adapters out of domain mutation.
    """

    status: ApplicationStatus = field(default_factory=ApplicationStatus)
    accounts: tuple[AccountIdentity, ...] = ()
    active_account: AccountIdentity | None = None
    draft: DraftIdentity | None = None
    recommendations: RecommendationState = field(default_factory=RecommendationState)
    pool: PoolState = field(default_factory=PoolState)
    progress: ProgressState | None = None
    errors: tuple[SessionError, ...] = ()
    build: BuildResult | None = None
    backtest: BacktestResult | None = None


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
