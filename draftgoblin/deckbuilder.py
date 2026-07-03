"""Deck-builder pair selection and plain-text build output.
Stage 1 picks a color pair from the drafted pool, not at draft start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, TypeAlias

from draftgoblin.carddb import CardDatabase
from draftgoblin.config import COLOR_PAIRS, DECK_BUILDER, DeckBuilderConfig
from draftgoblin.pickengine import PickEngine, ScoredCard
from draftgoblin.pool import DraftState, list_draft_states
from draftgoblin.seventeen import SEVENTEEN_LANDS_ATTRIBUTION, SeventeenLandsData

PathInput: TypeAlias = str | PathLike[str]


class DeckBuilderError(RuntimeError):
    """Raised when deck-builder input is missing or inconsistent.
    CLI callers surface this as a concise build diagnostic.
    """


@dataclass(frozen=True, slots=True)
class BuildPool:
    """A pool selected from a file or persisted draft state.
    The set code drives the offline 17Lands cache lookup.
    """

    set_code: str
    pool_grp_ids: tuple[int, ...]
    source_label: str
    account_id: str | None = None
    draft_id: str | None = None


@dataclass(frozen=True, slots=True)
class PairScore:
    """Computed score for one two-color pair.
    Card quality and aggregate 17Lands pair win rate stay visible separately.
    """

    pair: str
    playable_count: int
    playable_score_sum: float
    average_playable_score: float
    pair_win_rate: float | None
    pair_win_rate_score: float
    blended_score: float


@dataclass(frozen=True, slots=True)
class PairSelection:
    """Chosen pair plus sorted pair-score context.
    Forced selections keep the automatic best pair available for display.
    """

    chosen: PairScore
    runner_up: PairScore
    automatic: PairScore
    ranked_scores: tuple[PairScore, ...]
    forced_pair: str | None
    pool_size: int
    target_spell_count: int
    attribution: str = SEVENTEEN_LANDS_ATTRIBUTION

    @property
    def score_gap(self) -> float:
        """Return chosen score minus runner-up score.
        Forced selections can produce a negative gap by design.
        """

        return self.chosen.blended_score - self.runner_up.blended_score



def load_pool_file(*, path: PathInput, set_code: str | None = None) -> BuildPool:
    """Load a fixture pool JSON file for offline building.
    Draftgoblin state JSON and compact pool objects are both supported.
    """

    pool_path = Path(path)
    try:
        payload = json.loads(pool_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DeckBuilderError(f"Pool file does not exist at {pool_path}.") from error
    except json.JSONDecodeError as error:
        raise DeckBuilderError(f"Malformed pool file {pool_path}: {error}.") from error

    pool = _pool_from_payload(payload=payload, source_label=str(pool_path))
    resolved_set_code = _resolve_set_code(
        explicit_set_code=set_code,
        payload_set_code=pool.set_code,
        source_label=str(pool_path),
    )
    return BuildPool(
        set_code=resolved_set_code,
        pool_grp_ids=pool.pool_grp_ids,
        source_label=str(pool_path),
        account_id=pool.account_id,
        draft_id=pool.draft_id,
    )



def load_persisted_pool(
    *,
    app_dir: PathInput | None = None,
    account_id: str | None = None,
    draft_id: str | None = None,
) -> BuildPool:
    """Load the requested persisted pool, defaulting to the latest one.
    Account and draft filters disambiguate local multi-account state.
    """

    matches = _matching_states(
        states=list_draft_states(app_dir=app_dir),
        account_id=account_id,
        draft_id=draft_id,
    )
    if not matches:
        raise DeckBuilderError(
            _missing_persisted_pool_message(account_id=account_id, draft_id=draft_id)
        )

    if draft_id is not None and len(matches) > 1:
        raise DeckBuilderError(
            f"Multiple persisted pools use draft id {draft_id!r}; pass --account."
        )

    state = _latest_state(states=matches)
    return BuildPool(
        set_code=state.set_code,
        pool_grp_ids=state.pool_grp_ids,
        source_label=f"persisted {state.account_id}/{state.draft_id}",
        account_id=state.account_id,
        draft_id=state.draft_id,
    )



def select_color_pair(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData | None = None,
    forced_pair: str | None = None,
    config: DeckBuilderConfig = DECK_BUILDER,
) -> PairSelection:
    """Score all two-color pairs and choose the best or forced pair.
    The pool is already drafted; early draft picks remain rating-first elsewhere.
    """

    if config.target_spell_count <= 0:
        raise DeckBuilderError("Deck-builder target spell count must be greater than zero.")

    _validate_blending_weights(config=config)
    resolved_forced_pair = _optional_pair(value=forced_pair)
    scored_pool = PickEngine(ratings_data=ratings_data).score_pack(
        offered_grp_ids=pool_grp_ids,
        card_database=card_database,
        pool_grp_ids=(),
        pick_index=1,
    )
    scores = tuple(
        _score_pair(
            pair=pair,
            scored_cards=scored_pool.cards,
            ratings_data=ratings_data,
            config=config,
        )
        for pair in COLOR_PAIRS
    )
    ranked_scores = tuple(sorted(scores, key=_pair_score_sort_key))
    automatic = ranked_scores[0]
    chosen = (
        _score_for_pair(scores=scores, pair=resolved_forced_pair)
        if resolved_forced_pair is not None
        else automatic
    )
    runner_up = next(score for score in ranked_scores if score.pair != chosen.pair)
    return PairSelection(
        chosen=chosen,
        runner_up=runner_up,
        automatic=automatic,
        ranked_scores=ranked_scores,
        forced_pair=resolved_forced_pair,
        pool_size=len(pool_grp_ids),
        target_spell_count=config.target_spell_count,
        attribution=(
            ratings_data.attribution
            if ratings_data is not None
            else SEVENTEEN_LANDS_ATTRIBUTION
        ),
    )



def format_build_result(
    *,
    pool: BuildPool,
    selection: PairSelection,
    config: DeckBuilderConfig = DECK_BUILDER,
) -> str:
    """Format deterministic plain-text stage-1 build output.
    The report emphasizes pair selection after the drafted pool exists.
    """

    lines = [
        "Deck builder pair selection",
        f"Pool: {pool.source_label}",
        f"Set: {pool.set_code}",
        f"Pool size: {selection.pool_size} cards",
    ]
    if pool.account_id is not None:
        lines.append(f"Account: {pool.account_id}")

    if pool.draft_id is not None:
        lines.append(f"Draft: {pool.draft_id}")

    chosen_label = "forced" if selection.forced_pair is not None else "automatic"
    lines.extend(
        [
            f"Chosen pair: {selection.chosen.pair} ({chosen_label}, score "
            f"{_format_score(selection.chosen.blended_score, config=config)})",
            f"Runner-up: {selection.runner_up.pair} (score "
            f"{_format_score(selection.runner_up.blended_score, config=config)})",
            f"Score gap: {_format_score(selection.score_gap, config=config)}",
        ]
    )
    if selection.forced_pair is not None:
        lines.append(
            f"Best automatic pair: {selection.automatic.pair} (score "
            f"{_format_score(selection.automatic.blended_score, config=config)})"
        )

    lines.append("Pair scores:")
    for score in selection.ranked_scores:
        lines.append(
            _format_pair_score(
                score=score,
                target_spell_count=selection.target_spell_count,
                config=config,
            )
        )

    lines.append(selection.attribution)
    return "\n".join(lines) + "\n"



def _pool_from_payload(*, payload: Any, source_label: str) -> BuildPool:
    if isinstance(payload, dict):
        pool_value = payload.get("pool_grp_ids", payload.get("pool", payload.get("cards")))
        return BuildPool(
            set_code=_optional_string(payload.get("set_code")) or "",
            pool_grp_ids=_int_tuple(value=pool_value, field_name="pool_grp_ids"),
            source_label=source_label,
            account_id=_optional_string(payload.get("account_id")),
            draft_id=_optional_string(payload.get("draft_id")),
        )

    if isinstance(payload, list):
        return BuildPool(
            set_code="",
            pool_grp_ids=_int_tuple(value=payload, field_name="pool"),
            source_label=source_label,
        )

    raise DeckBuilderError("Pool file must contain a JSON object or list of grpIds.")



def _resolve_set_code(
    *,
    explicit_set_code: str | None,
    payload_set_code: str,
    source_label: str,
) -> str:
    set_code = explicit_set_code or payload_set_code
    if set_code == "":
        raise DeckBuilderError(
            f"Pool file {source_label} must include set_code or be used with --set-code."
        )

    return set_code.upper()



def _matching_states(
    *,
    states: tuple[DraftState, ...],
    account_id: str | None,
    draft_id: str | None,
) -> tuple[DraftState, ...]:
    return tuple(
        state
        for state in states
        if (account_id is None or state.account_id == account_id)
        and (draft_id is None or state.draft_id == draft_id)
    )



def _latest_state(*, states: tuple[DraftState, ...]) -> DraftState:
    return max(
        states,
        key=lambda state: (state.updated_at, state.account_id, state.draft_id),
    )



def _missing_persisted_pool_message(
    *,
    account_id: str | None,
    draft_id: str | None,
) -> str:
    if account_id is not None and draft_id is not None:
        return f"No persisted pool found for account {account_id!r} and draft {draft_id!r}."

    if account_id is not None:
        return f"No persisted pool found for account {account_id!r}."

    if draft_id is not None:
        return f"No persisted pool found for draft {draft_id!r}."

    return "No persisted pools found. Pass --pool or replay/watch a draft first."



def _score_pair(
    *,
    pair: str,
    scored_cards: tuple[ScoredCard, ...],
    ratings_data: SeventeenLandsData | None,
    config: DeckBuilderConfig,
) -> PairScore:
    playable_cards = tuple(
        card for card in scored_cards if _is_playable_in_pair(card=card, pair=pair)
    )
    top_cards = playable_cards[: config.target_spell_count]
    playable_score_sum = sum(card.raw_score for card in top_cards)
    average_playable_score = playable_score_sum / config.target_spell_count
    pair_win_rate = _pair_win_rate(pair=pair, ratings_data=ratings_data)
    pair_win_rate_score = _pair_win_rate_score(pair_win_rate=pair_win_rate, config=config)
    blended_score = _blended_score(
        average_playable_score=average_playable_score,
        pair_win_rate_score=pair_win_rate_score,
        config=config,
    )
    return PairScore(
        pair=pair,
        playable_count=len(playable_cards),
        playable_score_sum=playable_score_sum,
        average_playable_score=average_playable_score,
        pair_win_rate=pair_win_rate,
        pair_win_rate_score=pair_win_rate_score,
        blended_score=blended_score,
    )



def _is_playable_in_pair(*, card: ScoredCard, pair: str) -> bool:
    if not card.card.colors:
        return True

    return all(color in pair for color in card.card.colors)



def _pair_win_rate(*, pair: str, ratings_data: SeventeenLandsData | None) -> float | None:
    if ratings_data is None:
        return None

    pair_record = ratings_data.pair_win_rates.get(pair)
    if pair_record is None:
        return None

    return pair_record.win_rate



def _pair_win_rate_score(
    *,
    pair_win_rate: float | None,
    config: DeckBuilderConfig,
) -> float:
    win_rate = config.neutral_pair_win_rate if pair_win_rate is None else pair_win_rate
    return _clamp(value=win_rate * 100.0, lower=0.0, upper=100.0)



def _blended_score(
    *,
    average_playable_score: float,
    pair_win_rate_score: float,
    config: DeckBuilderConfig,
) -> float:
    weight_total = config.pair_score_card_weight + config.pair_score_win_rate_weight
    return (
        (average_playable_score * config.pair_score_card_weight)
        + (pair_win_rate_score * config.pair_score_win_rate_weight)
    ) / weight_total



def _validate_blending_weights(*, config: DeckBuilderConfig) -> None:
    if config.pair_score_card_weight < 0 or config.pair_score_win_rate_weight < 0:
        raise DeckBuilderError("Deck-builder blending weights must be non-negative.")

    if config.pair_score_card_weight + config.pair_score_win_rate_weight <= 0:
        raise DeckBuilderError("At least one deck-builder blending weight must be positive.")



def _optional_pair(value: str | None) -> str | None:
    if value is None:
        return None

    if value not in COLOR_PAIRS:
        raise DeckBuilderError(
            f"Invalid color pair {value!r}; expected one of {', '.join(COLOR_PAIRS)}."
        )

    return value



def _score_for_pair(*, scores: tuple[PairScore, ...], pair: str | None) -> PairScore:
    if pair is None:
        raise DeckBuilderError("Forced pair is missing.")

    for score in scores:
        if score.pair == pair:
            return score

    raise DeckBuilderError(f"No score was computed for pair {pair}.")



def _pair_score_sort_key(score: PairScore) -> tuple[float, float, float, int]:
    return (
        -score.blended_score,
        -score.playable_score_sum,
        -score.pair_win_rate_score,
        COLOR_PAIRS.index(score.pair),
    )



def _format_pair_score(
    *,
    score: PairScore,
    target_spell_count: int,
    config: DeckBuilderConfig,
) -> str:
    win_rate_label = _format_win_rate(score=score)
    return (
        f"- {score.pair}: score {_format_score(score.blended_score, config=config)}; "
        f"top {target_spell_count} sum "
        f"{_format_score(score.playable_score_sum, config=config)}; "
        f"17Lands WR {win_rate_label}; playables {score.playable_count}"
    )



def _format_win_rate(*, score: PairScore) -> str:
    if score.pair_win_rate is None:
        return f"neutral {score.pair_win_rate_score:.1f}%"

    return f"{score.pair_win_rate * 100.0:.1f}%"



def _format_score(value: float, *, config: DeckBuilderConfig) -> str:
    places = max(0, config.pair_score_decimal_places)
    return f"{value:.{places}f}"



def _int_tuple(*, value: Any, field_name: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise DeckBuilderError(f"Missing or invalid {field_name}; expected integer list.")

    result: list[int] = []
    for item in value:
        if isinstance(item, bool):
            raise DeckBuilderError(f"Missing or invalid {field_name}; expected integers.")

        try:
            result.append(int(item))
        except (TypeError, ValueError) as error:
            raise DeckBuilderError(
                f"Missing or invalid {field_name}; expected integers."
            ) from error

    return tuple(result)



def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise DeckBuilderError("Pool metadata values must be strings.")

    return value



def _clamp(*, value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)
