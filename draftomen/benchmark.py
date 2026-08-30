"""Offline pick-ranking benchmarks for public 17Lands draft data.
Compare raw 17Lands WR recommendations against Draftomen scores.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from os import PathLike
from typing import TypeAlias

from draftomen.carddb import CardDatabase, CardInfo
from draftomen.events import EXPECTED_PICKS_PER_PACK, EXPECTED_TOTAL_PICKS
from draftomen.pickengine import PickEngine, ScoredCard
from draftomen.ranking import rank_scored_cards, ranking_label
from draftomen.set_profile import SetProfile
from draftomen.seventeen import (
    SeventeenLandsData,
    iter_17lands_draft_data_rows,
)

PathInput: TypeAlias = str | PathLike[str]

BENCHMARK_RANKING_MODES = ("win_rate", "score")
PHASE_ORDER = ("open", "building", "locked")
PUBLIC_DRAFT_SOURCE = "17Lands public draft data"


class PickBenchmarkError(RuntimeError):
    """Raised when public draft data cannot be benchmarked.
    CLI callers surface this as a concise diagnostic.
    """


@dataclass(frozen=True, slots=True)
class PickBenchmarkRankResult:
    """Actual-pick rank for one ranking mode.
    Lower rank means the mode placed the trophy pick closer to the top.
    """

    ranking_mode: str
    actual_rank: int
    top_card: ScoredCard
    actual_card: ScoredCard


@dataclass(frozen=True, slots=True)
class PickBenchmarkPickResult:
    """One reconstructed trophy-draft pick decision.
    It stores ranks for each compared recommendation mode.
    """

    draft_id: str
    pack_number: int
    pick_number: int
    pick_index: int
    phase: str
    actual: CardInfo
    offered_count: int
    rankings: tuple[PickBenchmarkRankResult, ...]

    def rank_for(self, *, ranking_mode: str) -> PickBenchmarkRankResult | None:
        """Return the rank result for a mode when available.
        Benchmark rows normally carry both configured modes.
        """

        for result in self.rankings:
            if result.ranking_mode == ranking_mode:
                return result

        return None


@dataclass(frozen=True, slots=True)
class PickBenchmarkSummary:
    """Aggregate match metrics for one ranking mode.
    Top-N counts and average rank are enough for stable CLI reports.
    """

    ranking_mode: str
    pick_count: int
    top_1_count: int
    top_3_count: int
    top_5_count: int
    average_actual_pick_rank: float | None


@dataclass(frozen=True, slots=True)
class PickBenchmarkPhaseSummary:
    """Aggregate benchmark metrics for one commitment phase.
    Phases mirror the pick engine open/building/locked ramp.
    """

    phase: str
    summary: PickBenchmarkSummary


@dataclass(frozen=True, slots=True)
class PickBenchmarkComparison:
    """Direct DO-vs-17L rank comparison.
    Counts describe which mode ranked the actual pick better.
    """

    better_count: int
    same_count: int
    worse_count: int

    @property
    def total_count(self) -> int:
        """Return comparable rows for direct mode comparison.
        It is the denominator for better/same/worse percentages.
        """

        return self.better_count + self.same_count + self.worse_count


@dataclass(frozen=True, slots=True)
class PickBenchmarkReport:
    """Full offline benchmark report.
    Formatting stays separate so tests can inspect structured metrics.
    """

    set_code: str
    event_format: str
    source: str
    trophy_only: bool
    draft_count: int
    picks: tuple[PickBenchmarkPickResult, ...]
    skipped_reasons: tuple[tuple[str, int], ...]
    ranking_summaries: tuple[PickBenchmarkSummary, ...]
    phase_summaries: tuple[PickBenchmarkPhaseSummary, ...]
    comparison: PickBenchmarkComparison

    @property
    def skipped_count(self) -> int:
        """Return rows skipped after draft/result filtering.
        Reason counts are preserved for user-facing diagnostics.
        """

        return sum(count for _, count in self.skipped_reasons)


def generate_pick_benchmark_report(
    *,
    set_code: str,
    event_format: str,
    draft_data_file: PathInput,
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData,
    set_profile: SetProfile | None = None,
    max_drafts: int | None = None,
    trophy_only: bool = True,
) -> PickBenchmarkReport:
    """Benchmark recommendations from a local public draft-data dump.
    The file may be plain CSV, gzip CSV, or a tar archive containing CSV.
    """

    rows = iter_17lands_draft_data_rows(path=draft_data_file)
    return build_pick_benchmark_report_from_rows(
        set_code=set_code,
        event_format=event_format,
        rows=rows,
        card_database=card_database,
        ratings_data=ratings_data,
        set_profile=set_profile,
        source=str(draft_data_file),
        max_drafts=max_drafts,
        trophy_only=trophy_only,
    )


def build_pick_benchmark_report_from_rows(
    *,
    set_code: str,
    event_format: str,
    rows: Iterable[Mapping[str, str]],
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData,
    set_profile: SetProfile | None = None,
    source: str = PUBLIC_DRAFT_SOURCE,
    max_drafts: int | None = None,
    trophy_only: bool = True,
) -> PickBenchmarkReport:
    """Build a pick benchmark from already-loaded public draft rows.
    Tests use this path to avoid large fixture files and network access.
    """

    if max_drafts is not None and max_drafts <= 0:
        raise PickBenchmarkError("max_drafts must be greater than zero when provided.")

    effective_database = _database_with_rating_metadata(
        card_database=card_database,
        ratings_data=ratings_data,
    )
    name_index = _card_name_index(
        card_database=effective_database,
        ratings_data=ratings_data,
    )
    rows_by_draft = _rows_by_draft(
        rows=rows,
        set_code=set_code,
        event_format=event_format,
        max_drafts=max_drafts,
        trophy_only=trophy_only,
    )
    engine = PickEngine(ratings_data=ratings_data, set_profile=set_profile)
    picks: list[PickBenchmarkPickResult] = []
    skipped: Counter[str] = Counter()
    for draft_id, draft_rows in rows_by_draft.items():
        pool_grp_ids: list[int] = []
        for row in sorted(draft_rows, key=_draft_row_sort_key):
            result, skipped_reason = _score_benchmark_row(
                draft_id=draft_id,
                row=row,
                pool_grp_ids=tuple(pool_grp_ids),
                name_index=name_index,
                card_database=effective_database,
                pick_engine=engine,
            )
            if result is None:
                skipped[skipped_reason or "unscored row"] += 1
            else:
                picks.append(result)

            pool_grp_ids.extend(
                _resolved_actual_pick_grp_ids(row=row, name_index=name_index)
            )

    pick_results = tuple(picks)
    return PickBenchmarkReport(
        set_code=set_code.upper(),
        event_format=event_format,
        source=source,
        trophy_only=trophy_only,
        draft_count=len(rows_by_draft),
        picks=pick_results,
        skipped_reasons=tuple(sorted(skipped.items())),
        ranking_summaries=tuple(
            _summary_for_mode(picks=pick_results, ranking_mode=mode)
            for mode in BENCHMARK_RANKING_MODES
        ),
        phase_summaries=_phase_summaries(picks=pick_results),
        comparison=_compare_modes(picks=pick_results),
    )


def format_pick_benchmark_report(report: PickBenchmarkReport) -> str:
    """Format a pick benchmark report as stable plain text.
    The output is intentionally grep-friendly for comparing runs.
    """

    lines = [
        "Draft Omen trophy pick benchmark",
        f"Set: {report.set_code}",
        f"Format: {report.event_format}",
        f"Source: {report.source}",
        f"Draft filter: {_format_draft_filter(report=report)}",
        (
            "Rows: "
            f"{len(report.picks)} compared, "
            f"{report.skipped_count} skipped"
        ),
        (
            "Default ranking decision: DO Score is the default because "
            "public trophy benchmarks improved top-1/top-3/top-5 match "
            "rates and average actual-pick rank."
        ),
        "",
    ]
    lines.extend(_format_ranking_summary_table(summaries=report.ranking_summaries))
    lines.append("")
    lines.extend(_format_phase_summary_table(summaries=report.phase_summaries))
    lines.append("")
    lines.append(_format_comparison(comparison=report.comparison))
    lines.append(_format_heuristic_note(report=report))
    if report.skipped_reasons:
        lines.append(_format_skipped_reasons(report=report))

    return "\n".join(lines).rstrip() + "\n"


def _rows_by_draft(
    *,
    rows: Iterable[Mapping[str, str]],
    set_code: str,
    event_format: str,
    max_drafts: int | None,
    trophy_only: bool,
) -> dict[str, list[Mapping[str, str]]]:
    rows_by_draft: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        if not _draft_row_matches(
            row=row,
            set_code=set_code,
            event_format=event_format,
        ):
            continue

        if trophy_only and _event_match_wins(row=row) != _trophy_wins(
            event_format=event_format,
        ):
            continue

        draft_id = _clean_text(row.get("draft_id"))
        if draft_id is None:
            continue

        if draft_id not in rows_by_draft:
            if max_drafts is not None and len(rows_by_draft) >= max_drafts:
                continue

            rows_by_draft[draft_id] = []

        rows_by_draft[draft_id].append(row)

    return rows_by_draft


def _draft_row_matches(
    *,
    row: Mapping[str, str],
    set_code: str,
    event_format: str,
) -> bool:
    row_set = _clean_text(row.get("expansion"))
    if row_set is not None and row_set != set_code.upper():
        return False

    row_format = _clean_text(row.get("event_type"))
    return row_format is None or row_format == event_format


def _score_benchmark_row(
    *,
    draft_id: str,
    row: Mapping[str, str],
    pool_grp_ids: tuple[int, ...],
    name_index: Mapping[str, int],
    card_database: CardDatabase,
    pick_engine: PickEngine,
) -> tuple[PickBenchmarkPickResult | None, str | None]:
    actual_name = _clean_text(row.get("pick"))
    if actual_name is None:
        return None, "missing actual pick"

    actual_grp_id = _resolve_card_name(name=actual_name, name_index=name_index)
    if actual_grp_id is None:
        return None, "unresolved actual card"

    offered_grp_ids = _resolved_offered_grp_ids(row=row, name_index=name_index)
    if not offered_grp_ids:
        return None, "missing pack cards"

    if actual_grp_id not in offered_grp_ids:
        return None, "actual pick not in resolved pack"

    pack_number = _public_pack_number(row=row)
    pick_number = _public_pick_number(row=row)
    pick_index = _public_pick_index(
        pack_number=pack_number,
        pick_number=pick_number,
    )
    scored_pack = pick_engine.score_pack(
        offered_grp_ids=offered_grp_ids,
        card_database=card_database,
        pool_grp_ids=pool_grp_ids,
        pick_index=pick_index,
        pack_number=pack_number,
        pick_number=pick_number - 1,
        global_pick_index=pick_index,
        estimated_remaining_picks=max(0, EXPECTED_TOTAL_PICKS - pick_index),
    )
    ranking_results = tuple(
        _rank_result_for_mode(
            cards=scored_pack.cards,
            actual_grp_id=actual_grp_id,
            ranking_mode=mode,
        )
        for mode in BENCHMARK_RANKING_MODES
    )
    if any(result is None for result in ranking_results):
        return None, "actual pick not ranked"

    return (
        PickBenchmarkPickResult(
            draft_id=draft_id,
            pack_number=pack_number,
            pick_number=pick_number,
            pick_index=pick_index,
            phase=scored_pack.commitment.phase,
            actual=card_database.lookup(grp_id=actual_grp_id),
            offered_count=len(offered_grp_ids),
            rankings=tuple(
                result for result in ranking_results if result is not None
            ),
        ),
        None,
    )


def _rank_result_for_mode(
    *,
    cards: tuple[ScoredCard, ...],
    actual_grp_id: int,
    ranking_mode: str,
) -> PickBenchmarkRankResult | None:
    ranked = rank_scored_cards(cards=cards, ranking_mode=ranking_mode)
    if not ranked:
        return None

    for index, card in enumerate(ranked, start=1):
        if card.card.grp_id == actual_grp_id:
            return PickBenchmarkRankResult(
                ranking_mode=ranking_mode,
                actual_rank=index,
                top_card=ranked[0],
                actual_card=card,
            )

    return None


def _resolved_actual_pick_grp_ids(
    *,
    row: Mapping[str, str],
    name_index: Mapping[str, int],
) -> tuple[int, ...]:
    grp_ids: list[int] = []
    for key in ("pick", "pick_2"):
        name = _clean_text(row.get(key))
        if name is None:
            continue

        grp_id = _resolve_card_name(name=name, name_index=name_index)
        if grp_id is not None:
            grp_ids.append(grp_id)

    return tuple(grp_ids)


def _resolved_offered_grp_ids(
    *,
    row: Mapping[str, str],
    name_index: Mapping[str, int],
) -> tuple[int, ...]:
    grp_ids: list[int] = []
    seen: set[int] = set()
    for name in _offered_card_names(row=row):
        grp_id = _resolve_card_name(name=name, name_index=name_index)
        if grp_id is None or grp_id in seen:
            continue

        seen.add(grp_id)
        grp_ids.append(grp_id)

    return tuple(grp_ids)


def _offered_card_names(*, row: Mapping[str, str]) -> tuple[str, ...]:
    pack_card_names = tuple(
        key.removeprefix("pack_card_")
        for key, value in row.items()
        if key.startswith("pack_card_") and _pack_count_is_positive(value=value)
    )
    if pack_card_names:
        return pack_card_names

    return tuple(
        name
        for _, name in sorted(
            (
                (_available_card_column_index(key=key), value)
                for key, value in row.items()
                if key.startswith("available_card_") and _clean_text(value) is not None
            ),
            key=lambda item: item[0],
        )
    )


def _available_card_column_index(*, key: str) -> int:
    suffix = key.removeprefix("available_card_")
    try:
        return int(suffix)
    except ValueError:
        return 0


def _pack_count_is_positive(*, value: str) -> bool:
    text = _clean_text(value)
    if text is None:
        return False

    try:
        return float(text) > 0
    except ValueError:
        return text.casefold() in {"true", "yes"}


def _summary_for_mode(
    *,
    picks: tuple[PickBenchmarkPickResult, ...],
    ranking_mode: str,
) -> PickBenchmarkSummary:
    ranks = tuple(
        result.actual_rank
        for pick in picks
        if (result := pick.rank_for(ranking_mode=ranking_mode)) is not None
    )
    pick_count = len(ranks)
    average = (sum(ranks) / pick_count) if ranks else None
    return PickBenchmarkSummary(
        ranking_mode=ranking_mode,
        pick_count=pick_count,
        top_1_count=sum(1 for rank in ranks if rank <= 1),
        top_3_count=sum(1 for rank in ranks if rank <= 3),
        top_5_count=sum(1 for rank in ranks if rank <= 5),
        average_actual_pick_rank=average,
    )


def _phase_summaries(
    *,
    picks: tuple[PickBenchmarkPickResult, ...],
) -> tuple[PickBenchmarkPhaseSummary, ...]:
    summaries: list[PickBenchmarkPhaseSummary] = []
    for phase in PHASE_ORDER:
        phase_picks = tuple(pick for pick in picks if pick.phase == phase)
        if not phase_picks:
            continue

        for mode in BENCHMARK_RANKING_MODES:
            summaries.append(
                PickBenchmarkPhaseSummary(
                    phase=phase,
                    summary=_summary_for_mode(
                        picks=phase_picks,
                        ranking_mode=mode,
                    ),
                )
            )

    return tuple(summaries)


def _compare_modes(
    *,
    picks: tuple[PickBenchmarkPickResult, ...],
) -> PickBenchmarkComparison:
    better = 0
    same = 0
    worse = 0
    for pick in picks:
        win_rate = pick.rank_for(ranking_mode="win_rate")
        score = pick.rank_for(ranking_mode="score")
        if win_rate is None or score is None:
            continue

        if score.actual_rank < win_rate.actual_rank:
            better += 1
        elif score.actual_rank > win_rate.actual_rank:
            worse += 1
        else:
            same += 1

    return PickBenchmarkComparison(
        better_count=better,
        same_count=same,
        worse_count=worse,
    )


def _database_with_rating_metadata(
    *,
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData,
) -> CardDatabase:
    cards = dict(card_database.cards)
    for rating in ratings_data.ratings.values():
        existing = cards.get(rating.grp_id)
        if existing is not None and not existing.unknown:
            continue

        if rating.name.startswith("Unknown card "):
            continue

        cards[rating.grp_id] = CardInfo(
            grp_id=rating.grp_id,
            name=rating.name,
            colors=_rating_colors(color=rating.color),
            mana_value=None,
            rarity=rating.rarity or "unknown",
            types=("Unknown",),
            arena_id=rating.grp_id,
        )

    return replace(
        card_database,
        cards=cards,
        image_uris_by_name=dict(card_database.image_uris_by_name),
    )


def _card_name_index(
    *,
    card_database: CardDatabase,
    ratings_data: SeventeenLandsData,
) -> dict[str, int]:
    index: dict[str, int] = {}
    for card in card_database.cards.values():
        for name in _card_lookup_names(card=card):
            index.setdefault(_normalize_card_name(name), card.grp_id)

    for rating in ratings_data.ratings.values():
        if rating.name.startswith("Unknown card "):
            continue

        index.setdefault(_normalize_card_name(rating.name), rating.grp_id)

    return index


def _card_lookup_names(*, card: CardInfo) -> tuple[str, ...]:
    names = [card.name]
    names.extend(part.strip() for part in card.name.split("//") if part.strip())
    return tuple(dict.fromkeys(names))


def _resolve_card_name(
    *,
    name: str,
    name_index: Mapping[str, int],
) -> int | None:
    return name_index.get(_normalize_card_name(name))


def _normalize_card_name(name: str) -> str:
    return " ".join(name.casefold().split())


def _rating_colors(*, color: str | None) -> tuple[str, ...]:
    if color is None:
        return ()

    return tuple(symbol for symbol in "WUBRG" if symbol in color)


def _draft_row_sort_key(row: Mapping[str, str]) -> tuple[int, int]:
    return (_public_pack_number(row=row), _public_pick_number(row=row))


def _public_pack_number(*, row: Mapping[str, str]) -> int:
    return max(0, _optional_int(row.get("pack_number")) or 0)


def _public_pick_number(*, row: Mapping[str, str]) -> int:
    pick_number = _optional_int(row.get("pick_number"))
    if pick_number is None:
        return 1

    if pick_number <= 0:
        return pick_number + 1

    return pick_number


def _public_pick_index(*, pack_number: int, pick_number: int) -> int:
    return (max(0, pack_number) * EXPECTED_PICKS_PER_PACK) + max(1, pick_number)


def _event_match_wins(*, row: Mapping[str, str]) -> int | None:
    return _optional_int(row.get("event_match_wins"))


def _trophy_wins(*, event_format: str) -> int:
    if event_format.startswith("Trad"):
        return 3

    return 7


def _optional_int(value: str | None) -> int | None:
    text = _clean_text(value)
    if text is None:
        return None

    try:
        return int(float(text))
    except ValueError:
        return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None

    text = value.strip()
    if text == "":
        return None

    return text


def _format_draft_filter(*, report: PickBenchmarkReport) -> str:
    result_filter = "trophy drafts only" if report.trophy_only else "all matching drafts"
    return f"{report.draft_count} {result_filter}"


def _format_ranking_summary_table(
    *,
    summaries: tuple[PickBenchmarkSummary, ...],
) -> list[str]:
    if not summaries or all(summary.pick_count == 0 for summary in summaries):
        return ["No comparable picks were found."]

    lines = [
        "Ranking comparison:",
        "Ranking   Picks  Top-1   Top-3   Top-5   Avg actual-pick rank",
    ]
    for summary in summaries:
        lines.append(_format_summary_row(summary=summary))

    return lines


def _format_phase_summary_table(
    *,
    summaries: tuple[PickBenchmarkPhaseSummary, ...],
) -> list[str]:
    if not summaries:
        return ["Phase breakdown: no comparable picks."]

    lines = [
        "Phase breakdown:",
        "Phase     Ranking   Picks  Top-1   Top-3   Top-5   Avg rank",
    ]
    for phase_summary in summaries:
        summary = phase_summary.summary
        lines.append(
            f"{phase_summary.phase:<9} "
            f"{ranking_label(ranking_mode=summary.ranking_mode):<9} "
            f"{summary.pick_count:>5}  "
            f"{_format_top_rate(count=summary.top_1_count, total=summary.pick_count):>6}  "
            f"{_format_top_rate(count=summary.top_3_count, total=summary.pick_count):>6}  "
            f"{_format_top_rate(count=summary.top_5_count, total=summary.pick_count):>6}  "
            f"{_format_average(value=summary.average_actual_pick_rank):>8}"
        )

    return lines


def _format_summary_row(*, summary: PickBenchmarkSummary) -> str:
    return (
        f"{ranking_label(ranking_mode=summary.ranking_mode):<9} "
        f"{summary.pick_count:>5}  "
        f"{_format_top_rate(count=summary.top_1_count, total=summary.pick_count):>6}  "
        f"{_format_top_rate(count=summary.top_3_count, total=summary.pick_count):>6}  "
        f"{_format_top_rate(count=summary.top_5_count, total=summary.pick_count):>6}  "
        f"{_format_average(value=summary.average_actual_pick_rank):>20}"
    )


def _format_comparison(*, comparison: PickBenchmarkComparison) -> str:
    total = comparison.total_count
    if total == 0:
        return "DO vs 17L actual-pick rank: no comparable picks."

    return (
        "DO vs 17L actual-pick rank: "
        f"better {_format_count_rate(count=comparison.better_count, total=total)}, "
        f"same {_format_count_rate(count=comparison.same_count, total=total)}, "
        f"worse {_format_count_rate(count=comparison.worse_count, total=total)}."
    )


def _format_heuristic_note(*, report: PickBenchmarkReport) -> str:
    late_off_color_misses = 0
    neutral_prior_misses = 0
    for pick in report.picks:
        score = pick.rank_for(ranking_mode="score")
        if score is None or score.actual_rank <= 1:
            continue

        if (
            pick.phase in {"building", "locked"}
            and score.actual_card.color_fit == "off-color"
        ):
            late_off_color_misses += 1

        if score.actual_card.no_data:
            neutral_prior_misses += 1

    if late_off_color_misses > 0:
        return (
            "Non-ML heuristic candidate from misses: "
            f"{late_off_color_misses} DO Score misses were building/locked "
            "off-color trophy picks; tune the color commitment ramp or "
            "off-color penalty before ML work (#37)."
        )

    if neutral_prior_misses > 0:
        return (
            "Non-ML heuristic candidate from misses: "
            f"{neutral_prior_misses} DO Score misses used neutral-prior data; "
            "test ALSA and maindeck-rate weighting before ML work (#37)."
        )

    return (
        "Non-ML heuristic candidate from misses: review DO Score misses by "
        "phase for pair-specific pick priorities and maindeck-rate weighting "
        "before ML work (#37)."
    )


def _format_skipped_reasons(*, report: PickBenchmarkReport) -> str:
    reasons = "; ".join(
        f"{reason}: {count}"
        for reason, count in report.skipped_reasons
    )
    return f"Skipped rows: {report.skipped_count} ({reasons})."


def _format_top_rate(*, count: int, total: int) -> str:
    if total == 0:
        return "—"

    return f"{count / total:.1%}"


def _format_count_rate(*, count: int, total: int) -> str:
    return f"{count} ({count / total:.1%})"


def _format_average(*, value: float | None) -> str:
    if value is None:
        return "—"

    return f"{value:.2f}"


