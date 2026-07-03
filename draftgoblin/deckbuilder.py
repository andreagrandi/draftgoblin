"""Deck-builder pair selection, constrained spell fill, and text output.
Stage 2 selects 23 spells under documented Limited structure defaults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, TypeAlias

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import COLOR_PAIRS, DECK_BUILDER, DeckBuilderConfig
from draftgoblin.pickengine import PickEngine, ScoredCard
from draftgoblin.pool import DraftState, list_draft_states
from draftgoblin.seventeen import SEVENTEEN_LANDS_ATTRIBUTION, SeventeenLandsData

PathInput: TypeAlias = str | PathLike[str]
SPELL_TYPE_MARKERS = (
    "Creature",
    "Artifact",
    "Enchantment",
    "Planeswalker",
    "Battle",
    "Instant",
    "Sorcery",
)


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


@dataclass(frozen=True, slots=True)
class SpellCounts:
    """Structural counts for a selected spell set.
    Derived properties keep constraint checks readable and deterministic.
    """

    total: int
    creatures: int
    two_drops: int
    expensive: int

    @property
    def noncreatures(self) -> int:
        """Return selected cards that are not creatures.
        This is used when enforcing the creature ceiling.
        """

        return self.total - self.creatures

    @property
    def non_expensive(self) -> int:
        """Return selected cards below the expensive-spell threshold.
        This is used when enforcing the high-mana-value soft cap.
        """

        return self.total - self.expensive


@dataclass(frozen=True, slots=True)
class SpellConstraints:
    """Effective constraints for a spell-selection attempt.
    Values may be relaxed when the pool cannot satisfy configured defaults.
    """

    spell_count: int
    creature_floor: int
    creature_ceiling: int
    minimum_two_drops: int
    maximum_expensive_spells: int


@dataclass(frozen=True, slots=True)
class SpellSelection:
    """Selected spells plus bench and constraint metadata.
    The requested count remains visible when a tiny fixture cannot make 23.
    """

    pair: str
    spells: tuple[ScoredCard, ...]
    bench: tuple[ScoredCard, ...]
    eligible_count: int
    requested_spell_count: int
    constraints: SpellConstraints
    counts: SpellCounts
    applied_relaxations: tuple[str, ...]
    allow_splash_requested: bool


@dataclass(frozen=True, slots=True)
class _ConstraintPlan:
    """One point in the documented stage-2 relaxation order.
    Later plans disable progressively more structural constraints.
    """

    enforce_expensive_cap: bool
    enforce_two_drop_minimum: bool
    enforce_creature_ceiling: bool
    enforce_creature_floor: bool



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

    _validate_deck_builder_config(config=config)
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



def select_deck_spells(
    *,
    pool_grp_ids: tuple[int, ...],
    card_database: CardDatabase,
    pair: str,
    ratings_data: SeventeenLandsData | None = None,
    allow_splash: bool = False,
    config: DeckBuilderConfig = DECK_BUILDER,
) -> SpellSelection:
    """Select deck spells for a chosen pair under structural constraints.
    The v1 splash flag is accepted but intentionally does not alter eligibility.
    """

    _validate_deck_builder_config(config=config)
    resolved_pair = _optional_pair(value=pair)
    if resolved_pair is None:
        raise DeckBuilderError("A color pair is required before selecting spells.")

    scored_pool = PickEngine(ratings_data=ratings_data).score_pack(
        offered_grp_ids=pool_grp_ids,
        card_database=card_database,
        pool_grp_ids=(),
        pick_index=1,
    )
    candidates = tuple(
        card
        for card in scored_pool.cards
        if _is_eligible_spell_for_pair(card=card, pair=resolved_pair)
    )

    for plan in _constraint_plans():
        constraints = _constraints_for_plan(
            candidates=candidates,
            plan=plan,
            config=config,
        )
        selected = _select_with_constraints(
            candidates=candidates,
            constraints=constraints,
            config=config,
        )
        if selected is None:
            continue

        counts = _spell_counts(cards=selected, config=config)
        return SpellSelection(
            pair=resolved_pair,
            spells=selected,
            bench=_bench_cards(
                candidates=candidates,
                selected=selected,
                config=config,
            ),
            eligible_count=len(candidates),
            requested_spell_count=config.target_spell_count,
            constraints=constraints,
            counts=counts,
            applied_relaxations=_applied_relaxations(
                plan=plan,
                constraints=constraints,
                config=config,
            ),
            allow_splash_requested=allow_splash,
        )

    raise DeckBuilderError("Could not select deck spells with the configured constraints.")



def format_build_result(
    *,
    pool: BuildPool,
    selection: PairSelection,
    spell_selection: SpellSelection | None = None,
    config: DeckBuilderConfig = DECK_BUILDER,
) -> str:
    """Format deterministic plain-text deck-builder output.
    Pair selection remains first, followed by the constrained spell sheet.
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

    if spell_selection is not None:
        lines.extend(_format_spell_selection(selection=spell_selection, config=config))

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
        card for card in scored_cards if _is_eligible_spell_for_pair(card=card, pair=pair)
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



def _is_eligible_spell_for_pair(*, card: ScoredCard, pair: str) -> bool:
    return _is_spell_card(card=card.card) and _is_playable_in_pair(card=card, pair=pair)



def _is_playable_in_pair(*, card: ScoredCard, pair: str) -> bool:
    if not card.card.colors:
        return True

    return all(color in pair for color in card.card.colors)



def _is_spell_card(*, card: CardInfo) -> bool:
    for type_line in card.types:
        faces = tuple(part.strip() for part in type_line.split("//"))
        if any(_type_face_is_spell(face=face) for face in faces):
            return True

    return False



def _type_face_is_spell(*, face: str) -> bool:
    if "Land" in face:
        return False

    return any(marker in face for marker in SPELL_TYPE_MARKERS)



def _is_creature_card(*, card: CardInfo) -> bool:
    return any("Creature" in type_line for type_line in card.types)



def _is_two_drop(*, card: ScoredCard, config: DeckBuilderConfig) -> bool:
    return card.card.mana_value == config.two_drop_mana_value



def _is_expensive_spell(*, card: ScoredCard, config: DeckBuilderConfig) -> bool:
    mana_value = card.card.mana_value
    return mana_value is not None and mana_value >= config.expensive_spell_mana_value



def _constraints_for_plan(
    *,
    candidates: tuple[ScoredCard, ...],
    plan: _ConstraintPlan,
    config: DeckBuilderConfig,
) -> SpellConstraints:
    target = min(config.target_spell_count, len(candidates))
    pool_counts = _spell_counts(cards=candidates, config=config)

    creature_floor = (
        min(config.creature_floor, pool_counts.creatures, target)
        if plan.enforce_creature_floor
        else 0
    )
    if plan.enforce_creature_ceiling:
        requested_ceiling = min(config.creature_ceiling, target)
        needed_creatures = max(0, target - pool_counts.noncreatures)
        creature_ceiling = min(
            target,
            max(requested_ceiling, needed_creatures, creature_floor),
        )
    else:
        creature_ceiling = target

    minimum_two_drops = (
        min(config.minimum_two_drops, pool_counts.two_drops, target)
        if plan.enforce_two_drop_minimum
        else 0
    )
    if plan.enforce_expensive_cap:
        requested_cap = min(config.maximum_expensive_spells, target)
        needed_expensive = max(0, target - pool_counts.non_expensive)
        maximum_expensive_spells = min(target, max(requested_cap, needed_expensive))
    else:
        maximum_expensive_spells = target

    return SpellConstraints(
        spell_count=target,
        creature_floor=creature_floor,
        creature_ceiling=creature_ceiling,
        minimum_two_drops=minimum_two_drops,
        maximum_expensive_spells=maximum_expensive_spells,
    )



def _constraint_plans() -> tuple[_ConstraintPlan, ...]:
    return (
        _ConstraintPlan(
            enforce_expensive_cap=True,
            enforce_two_drop_minimum=True,
            enforce_creature_ceiling=True,
            enforce_creature_floor=True,
        ),
        _ConstraintPlan(
            enforce_expensive_cap=False,
            enforce_two_drop_minimum=True,
            enforce_creature_ceiling=True,
            enforce_creature_floor=True,
        ),
        _ConstraintPlan(
            enforce_expensive_cap=False,
            enforce_two_drop_minimum=False,
            enforce_creature_ceiling=True,
            enforce_creature_floor=True,
        ),
        _ConstraintPlan(
            enforce_expensive_cap=False,
            enforce_two_drop_minimum=False,
            enforce_creature_ceiling=False,
            enforce_creature_floor=True,
        ),
        _ConstraintPlan(
            enforce_expensive_cap=False,
            enforce_two_drop_minimum=False,
            enforce_creature_ceiling=False,
            enforce_creature_floor=False,
        ),
    )



def _select_with_constraints(
    *,
    candidates: tuple[ScoredCard, ...],
    constraints: SpellConstraints,
    config: DeckBuilderConfig,
) -> tuple[ScoredCard, ...] | None:
    selected: list[ScoredCard] = []
    remaining = list(candidates)
    while len(selected) < constraints.spell_count and remaining:
        counts = _spell_counts(cards=tuple(selected), config=config)
        floor_unmet = counts.creatures < constraints.creature_floor
        ordered_indices = sorted(
            range(len(remaining)),
            key=lambda index: _candidate_selection_sort_key(
                card=remaining[index],
                floor_unmet=floor_unmet,
                config=config,
            ),
        )
        picked_index = _first_feasible_index(
            ordered_indices=tuple(ordered_indices),
            selected=tuple(selected),
            remaining=tuple(remaining),
            constraints=constraints,
            config=config,
        )
        if picked_index is None:
            return None

        selected.append(remaining.pop(picked_index))

    result = tuple(selected)
    if _counts_satisfy_constraints(
        counts=_spell_counts(cards=result, config=config),
        constraints=constraints,
    ):
        return result

    return None



def _first_feasible_index(
    *,
    ordered_indices: tuple[int, ...],
    selected: tuple[ScoredCard, ...],
    remaining: tuple[ScoredCard, ...],
    constraints: SpellConstraints,
    config: DeckBuilderConfig,
) -> int | None:
    for index in ordered_indices:
        remaining_after = tuple(
            card for item_index, card in enumerate(remaining) if item_index != index
        )
        if _can_add_spell(
            candidate=remaining[index],
            selected=selected,
            remaining_after=remaining_after,
            constraints=constraints,
            config=config,
        ):
            return index

    return None



def _can_add_spell(
    *,
    candidate: ScoredCard,
    selected: tuple[ScoredCard, ...],
    remaining_after: tuple[ScoredCard, ...],
    constraints: SpellConstraints,
    config: DeckBuilderConfig,
) -> bool:
    next_selected = (*selected, candidate)
    counts = _spell_counts(cards=next_selected, config=config)
    if counts.total > constraints.spell_count:
        return False

    if counts.creatures > constraints.creature_ceiling:
        return False

    if counts.expensive > constraints.maximum_expensive_spells:
        return False

    return _can_complete_selection(
        counts=counts,
        remaining=remaining_after,
        constraints=constraints,
        config=config,
    )



def _can_complete_selection(
    *,
    counts: SpellCounts,
    remaining: tuple[ScoredCard, ...],
    constraints: SpellConstraints,
    config: DeckBuilderConfig,
) -> bool:
    slots_remaining = constraints.spell_count - counts.total
    if slots_remaining == 0:
        return _counts_satisfy_constraints(counts=counts, constraints=constraints)

    if len(remaining) < slots_remaining:
        return False

    states = {(0, 0, 0, 0)}
    creature_room = constraints.creature_ceiling - counts.creatures
    expensive_room = constraints.maximum_expensive_spells - counts.expensive
    for card in remaining:
        creature = 1 if _is_creature_card(card=card.card) else 0
        two_drop = 1 if _is_two_drop(card=card, config=config) else 0
        expensive = 1 if _is_expensive_spell(card=card, config=config) else 0
        next_states = set(states)
        for selected_count, creatures, two_drops, expensive_spells in states:
            if selected_count >= slots_remaining:
                continue

            next_creatures = creatures + creature
            next_expensive = expensive_spells + expensive
            if next_creatures > creature_room or next_expensive > expensive_room:
                continue

            next_states.add(
                (
                    selected_count + 1,
                    next_creatures,
                    min(constraints.minimum_two_drops, two_drops + two_drop),
                    next_expensive,
                )
            )

        states = next_states

    for selected_count, creatures, two_drops, expensive_spells in states:
        if selected_count != slots_remaining:
            continue

        final_counts = SpellCounts(
            total=constraints.spell_count,
            creatures=counts.creatures + creatures,
            two_drops=counts.two_drops + two_drops,
            expensive=counts.expensive + expensive_spells,
        )
        if _counts_satisfy_constraints(counts=final_counts, constraints=constraints):
            return True

    return False



def _candidate_selection_sort_key(
    *,
    card: ScoredCard,
    floor_unmet: bool,
    config: DeckBuilderConfig,
) -> tuple[float, int, float, float, int]:
    effective_score = card.raw_score
    creature_preference = 0
    if floor_unmet:
        creature = _is_creature_card(card=card.card)
        if creature:
            effective_score += config.near_tie_creature_preference_points
        creature_preference = 0 if creature else 1

    return (
        -effective_score,
        creature_preference,
        -card.raw_score,
        -card.base_rating,
        card.original_index,
    )



def _bench_cards(
    *,
    candidates: tuple[ScoredCard, ...],
    selected: tuple[ScoredCard, ...],
    config: DeckBuilderConfig,
) -> tuple[ScoredCard, ...]:
    if config.bench_card_count <= 0:
        return ()

    selected_indices = {card.original_index for card in selected}
    unselected = tuple(
        card for card in candidates if card.original_index not in selected_indices
    )
    return tuple(sorted(unselected, key=_bench_sort_key)[: config.bench_card_count])



def _bench_sort_key(card: ScoredCard) -> tuple[int, float, float, int]:
    return (-card.score, -card.raw_score, -card.base_rating, card.original_index)



def _spell_counts(
    *,
    cards: tuple[ScoredCard, ...],
    config: DeckBuilderConfig,
) -> SpellCounts:
    return SpellCounts(
        total=len(cards),
        creatures=sum(1 for card in cards if _is_creature_card(card=card.card)),
        two_drops=sum(1 for card in cards if _is_two_drop(card=card, config=config)),
        expensive=sum(
            1 for card in cards if _is_expensive_spell(card=card, config=config)
        ),
    )



def _counts_satisfy_constraints(
    *,
    counts: SpellCounts,
    constraints: SpellConstraints,
) -> bool:
    return (
        counts.total == constraints.spell_count
        and counts.creatures >= constraints.creature_floor
        and counts.creatures <= constraints.creature_ceiling
        and counts.two_drops >= constraints.minimum_two_drops
        and counts.expensive <= constraints.maximum_expensive_spells
    )



def _applied_relaxations(
    *,
    plan: _ConstraintPlan,
    constraints: SpellConstraints,
    config: DeckBuilderConfig,
) -> tuple[str, ...]:
    relaxations: list[str] = []
    default_expensive_cap = min(config.maximum_expensive_spells, constraints.spell_count)
    if (
        not plan.enforce_expensive_cap
        or constraints.maximum_expensive_spells > default_expensive_cap
    ):
        relaxations.append(config.relaxation_order[0])

    default_two_drops = min(config.minimum_two_drops, constraints.spell_count)
    if (
        not plan.enforce_two_drop_minimum
        or constraints.minimum_two_drops < default_two_drops
    ):
        relaxations.append(config.relaxation_order[1])

    default_creature_ceiling = min(config.creature_ceiling, constraints.spell_count)
    if (
        not plan.enforce_creature_ceiling
        or constraints.creature_ceiling > default_creature_ceiling
    ):
        relaxations.append(config.relaxation_order[2])

    default_creature_floor = min(config.creature_floor, constraints.spell_count)
    if (
        not plan.enforce_creature_floor
        or constraints.creature_floor < default_creature_floor
    ):
        relaxations.append(config.relaxation_order[3])

    if constraints.spell_count < config.target_spell_count:
        relaxations.append(config.relaxation_order[4])

    return tuple(dict.fromkeys(relaxations))



def _format_spell_selection(
    *,
    selection: SpellSelection,
    config: DeckBuilderConfig,
) -> list[str]:
    counts = selection.counts
    constraints = selection.constraints
    lines = [
        "",
        "Spell selection:",
        f"Eligible spells for {selection.pair}: {selection.eligible_count}",
        f"Selected spells: {counts.total}/{selection.requested_spell_count}",
        f"Creature count: {counts.creatures} "
        f"(target {constraints.creature_floor}-{constraints.creature_ceiling})",
        f"Two-drops MV {config.two_drop_mana_value:g}: {counts.two_drops} "
        f"(minimum {constraints.minimum_two_drops})",
        f"Expensive spells MV >= {config.expensive_spell_mana_value:g}: "
        f"{counts.expensive} (soft cap {constraints.maximum_expensive_spells})",
        _format_splash_note(selection=selection),
        f"Relaxation order: {' -> '.join(config.relaxation_order)}",
        f"Applied relaxations: {_format_relaxations(selection.applied_relaxations)}",
        "Creatures:",
    ]
    lines.extend(
        _format_spell_card(card=card)
        for card in _sorted_spell_cards(cards=selection.spells, creatures=True)
    )
    lines.append("Non-creatures:")
    lines.extend(
        _format_spell_card(card=card)
        for card in _sorted_spell_cards(cards=selection.spells, creatures=False)
    )
    lines.append("Bench:")
    if selection.bench:
        lines.extend(
            _format_bench_card(card=card, selection=selection, config=config)
            for card in selection.bench
        )
    else:
        lines.append("- none")

    return lines



def _format_splash_note(*, selection: SpellSelection) -> str:
    if selection.allow_splash_requested:
        return "Splash: --allow-splash accepted but inert in v1; off-pair cards excluded"

    return "Splash: disabled (--allow-splash is inert in v1; off-pair cards excluded)"



def _format_relaxations(relaxations: tuple[str, ...]) -> str:
    if not relaxations:
        return "none"

    return ", ".join(relaxations)



def _sorted_spell_cards(
    *,
    cards: tuple[ScoredCard, ...],
    creatures: bool,
) -> tuple[ScoredCard, ...]:
    matching = tuple(
        card for card in cards if _is_creature_card(card=card.card) == creatures
    )
    return tuple(sorted(matching, key=_curve_sort_key))



def _curve_sort_key(card: ScoredCard) -> tuple[float, str, int]:
    mana_value = 99.0 if card.card.mana_value is None else card.card.mana_value
    return (mana_value, card.card.name, card.original_index)



def _format_spell_card(*, card: ScoredCard) -> str:
    return (
        f"- MV {_format_mana_value(card.card.mana_value)} | "
        f"score {card.score} | {card.card.name} ({_format_colors(card.card.colors)})"
    )



def _format_bench_card(
    *,
    card: ScoredCard,
    selection: SpellSelection,
    config: DeckBuilderConfig,
) -> str:
    return f"{_format_spell_card(card=card)} ({_bench_reason(card=card, selection=selection, config=config)})"



def _bench_reason(
    *,
    card: ScoredCard,
    selection: SpellSelection,
    config: DeckBuilderConfig,
) -> str:
    if (
        _is_expensive_spell(card=card, config=config)
        and selection.counts.expensive >= selection.constraints.maximum_expensive_spells
    ):
        return "cut: expensive-spell cap"

    if (
        _is_creature_card(card=card.card)
        and selection.counts.creatures >= selection.constraints.creature_ceiling
    ):
        return "cut: creature ceiling"

    return "cut: lower score"



def _format_mana_value(mana_value: float | None) -> str:
    if mana_value is None:
        return "?"

    return f"{mana_value:g}"



def _format_colors(colors: tuple[str, ...]) -> str:
    if not colors:
        return "C"

    return "".join(colors)



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



def _validate_deck_builder_config(*, config: DeckBuilderConfig) -> None:
    if config.target_spell_count <= 0:
        raise DeckBuilderError("Deck-builder target spell count must be greater than zero.")

    if config.creature_floor < 0 or config.creature_ceiling < 0:
        raise DeckBuilderError("Deck-builder creature constraints must be non-negative.")

    if config.creature_floor > config.creature_ceiling:
        raise DeckBuilderError("Deck-builder creature floor cannot exceed its ceiling.")

    if config.minimum_two_drops < 0 or config.maximum_expensive_spells < 0:
        raise DeckBuilderError("Deck-builder curve constraints must be non-negative.")

    if config.near_tie_creature_preference_points < 0:
        raise DeckBuilderError("Deck-builder near-tie preference must be non-negative.")

    if len(config.relaxation_order) < 5:
        raise DeckBuilderError("Deck-builder relaxation order must describe all stages.")



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
