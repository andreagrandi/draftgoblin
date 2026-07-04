from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.config import COLOR_PAIRS, PICK_ENGINE
from draftgoblin.seventeen import (
    NEUTRAL_PRIOR_SOURCE,
    PREMIER_DRAFT_FORMAT,
    QUICK_DRAFT_FORMAT,
    SEVENTEEN_LANDS_ATTRIBUTION,
    SeventeenLandsError,
    build_17lands_structure_targets_from_draft_rows,
    load_17lands_structure_targets,
    load_cached_17lands_data,
    load_or_refresh_17lands_data,
    load_or_refresh_17lands_format_data,
    save_17lands_structure_targets,
    seventeen_lands_cache_path,
    seventeen_lands_pair_card_cache_path,
    seventeen_lands_structure_targets_cache_path,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
QUICK_CARD_RATINGS_PATH = FIXTURE_DIR / "17lands-card-ratings-quick.json"
PREMIER_CARD_RATINGS_PATH = FIXTURE_DIR / "17lands-card-ratings-premier.json"
COLOR_RATINGS_PATH = FIXTURE_DIR / "17lands-color-ratings.json"


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RecordingFetcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.urls: list[str] = []
        self.quick_card_ratings = _load_json(path=QUICK_CARD_RATINGS_PATH)
        self.premier_card_ratings = _load_json(path=PREMIER_CARD_RATINGS_PATH)
        self.pair_card_ratings = [_pair_card_rating_row()]
        self.color_ratings = _load_json(path=COLOR_RATINGS_PATH)

    def __call__(self, url: str, timeout_seconds: int) -> Any:
        self.urls.append(url)
        if self.fail:
            raise SeventeenLandsError("network unavailable")

        if "/card_ratings/data" in url and "colors=WU" in url:
            return self.pair_card_ratings

        if "/card_ratings/data" in url and "event_type=PremierDraft" in url:
            return self.premier_card_ratings

        if "/card_ratings/data" in url:
            return self.quick_card_ratings

        if "/color_ratings/data" in url:
            return self.color_ratings

        raise AssertionError(f"unexpected URL {url}")


def test_17lands_format_data_is_cached_and_not_refetched_within_24h(
    tmp_path: Path,
) -> None:
    clock = FrozenClock(datetime(2026, 7, 3, 12, 0, tzinfo=UTC))
    fetcher = RecordingFetcher()

    first = load_or_refresh_17lands_format_data(
        set_code="tst",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=clock,
        fetch_json=fetcher,
    )
    clock.now = clock.now + timedelta(hours=23, minutes=59)
    second = load_or_refresh_17lands_format_data(
        set_code="tst",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=clock,
        fetch_json=fetcher,
    )

    assert len(fetcher.urls) == 2
    assert second.fetched_at == first.fetched_at
    assert second.card_ratings[1001].gih_win_rate == 0.61
    assert seventeen_lands_cache_path(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
    ).exists()


def test_stale_17lands_cache_serves_last_good_data_when_offline(
    tmp_path: Path,
) -> None:
    clock = FrozenClock(datetime(2026, 7, 3, 12, 0, tzinfo=UTC))
    first = load_or_refresh_17lands_format_data(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=clock,
        fetch_json=RecordingFetcher(),
    )
    clock.now = clock.now + timedelta(hours=25)
    offline_fetcher = RecordingFetcher(fail=True)

    stale = load_or_refresh_17lands_format_data(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=clock,
        fetch_json=offline_fetcher,
    )

    assert len(offline_fetcher.urls) == 1
    assert stale.fetched_at == first.fetched_at
    assert stale.card_ratings[1001].name == "Fixture Quick Bomb"


def test_quickdraft_ratings_fall_back_to_premier_then_neutral_prior(
    tmp_path: Path,
) -> None:
    data = load_or_refresh_17lands_data(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=FrozenClock(datetime(2026, 7, 3, 12, 0, tzinfo=UTC)),
        fetch_json=RecordingFetcher(),
        thin_sample_minimum=500,
    )

    quick_rating = data.rating_for(grp_id=1001)
    thin_fallback = data.rating_for(grp_id=1002)
    missing_fallback = data.rating_for(grp_id=1003)
    neutral = data.rating_for(grp_id=9999)

    assert quick_rating.gih_win_rate == 0.61
    assert quick_rating.metadata.source_format == QUICK_DRAFT_FORMAT
    assert quick_rating.metadata.fallback_reason is None

    assert thin_fallback.gih_win_rate == 0.55
    assert thin_fallback.metadata.source_format == PREMIER_DRAFT_FORMAT
    assert thin_fallback.metadata.fallback_reason == "primary-thin"

    assert missing_fallback.name == "Fixture Premier Only Card"
    assert missing_fallback.metadata.source_format == PREMIER_DRAFT_FORMAT
    assert missing_fallback.metadata.fallback_reason == "primary-missing"

    assert neutral.neutral_prior is True
    assert neutral.metadata.source == NEUTRAL_PRIOR_SOURCE
    assert neutral.metadata.fallback_reason == "fallback-missing"
    assert neutral.neutral_prior_score == PICK_ENGINE.neutral_prior_score
    assert data.attribution == SEVENTEEN_LANDS_ATTRIBUTION


def test_cached_17lands_data_uses_empty_primary_when_cache_is_missing(
    tmp_path: Path,
) -> None:
    data = load_cached_17lands_data(set_code="TST", app_dir=tmp_path)

    rating = data.rating_for(grp_id=9999)

    assert data.primary.card_ratings == {}
    assert data.fallback is None
    assert rating.neutral_prior is True
    assert rating.metadata.source == NEUTRAL_PRIOR_SOURCE


def test_pair_win_rates_are_available_for_all_ten_pairs(tmp_path: Path) -> None:
    dataset = load_or_refresh_17lands_format_data(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=FrozenClock(datetime(2026, 7, 3, 12, 0, tzinfo=UTC)),
        fetch_json=RecordingFetcher(),
    )

    assert set(dataset.pair_win_rates) == set(COLOR_PAIRS)
    assert dataset.pair_win_rates["WU"].wins == 60
    assert dataset.pair_win_rates["WU"].games == 100
    assert dataset.pair_win_rates["WU"].win_rate == 0.6
    assert dataset.attribution == SEVENTEEN_LANDS_ATTRIBUTION


def test_pair_filtered_card_ratings_are_loaded_lazily_and_cached(
    tmp_path: Path,
) -> None:
    fetcher = RecordingFetcher()
    data = load_or_refresh_17lands_data(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
        clock=FrozenClock(datetime(2026, 7, 3, 12, 0, tzinfo=UTC)),
        fetch_json=fetcher,
        thin_sample_minimum=500,
    )

    assert len(fetcher.urls) == 4

    pair_rating = data.pair_rating_for(grp_id=1002, pair="WU")
    second_pair_rating = data.pair_rating_for(grp_id=1002, pair="WU")
    cached_data = load_cached_17lands_data(set_code="TST", app_dir=tmp_path)
    cached_pair_rating = cached_data.pair_rating_for(grp_id=1002, pair="WU")

    assert pair_rating.gih_win_rate == 0.66
    assert second_pair_rating.gih_win_rate == 0.66
    assert cached_pair_rating.gih_win_rate == 0.66
    assert "WU" in data.pair_card_ratings
    assert len(fetcher.urls) == 5
    assert any("colors=WU" in url for url in fetcher.urls)
    assert seventeen_lands_pair_card_cache_path(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        pair="WU",
        app_dir=tmp_path,
    ).exists()


def test_structure_targets_are_computed_cached_and_loaded_with_ratings(
    tmp_path: Path,
) -> None:
    computed_at = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    targets = build_17lands_structure_targets_from_draft_rows(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        card_database=_structure_card_database(),
        rows=_structure_rows(),
        source_url="https://17lands-public.example/draft.csv.gz",
        computed_at=computed_at,
    )

    save_17lands_structure_targets(targets, app_dir=tmp_path)
    loaded_targets = load_17lands_structure_targets(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
    )
    data = load_cached_17lands_data(set_code="TST", app_dir=tmp_path)
    wu_targets = data.structure_targets_for(pair="WU")

    assert loaded_targets.total_decks == 2
    assert set(loaded_targets.targets) == {"WU"}
    assert wu_targets is not None
    assert wu_targets.sample_size == 2
    assert wu_targets.average_creature_count == 15.0
    assert wu_targets.average_land_count == 16.5
    assert wu_targets.average_two_drop_count == 6.0
    assert seventeen_lands_structure_targets_cache_path(
        set_code="TST",
        event_format=QUICK_DRAFT_FORMAT,
        app_dir=tmp_path,
    ).exists()


def _load_json(*, path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _structure_card_database() -> CardDatabase:
    cards: dict[int, CardInfo] = {}
    for offset in range(16):
        grp_id = 2000 + offset
        cards[grp_id] = _structure_card(
            grp_id=grp_id,
            name=f"Structure Creature {offset}",
            colors=("W",) if offset % 2 == 0 else ("U",),
            mana_value=2.0 if offset < 6 else 3.0,
            types=("Creature — Fixture",),
        )

    for offset in range(8):
        grp_id = 2100 + offset
        cards[grp_id] = _structure_card(
            grp_id=grp_id,
            name=f"Structure Spell {offset}",
            colors=("W",) if offset % 2 == 0 else ("U",),
            mana_value=3.0,
            types=("Instant",),
        )

    cards[2200] = _structure_card(
        grp_id=2200,
        name="Ignored Red Card",
        colors=("R",),
        mana_value=2.0,
        types=("Creature — Fixture",),
    )
    return CardDatabase(cards=cards)


def _structure_card(
    *,
    grp_id: int,
    name: str,
    colors: tuple[str, ...],
    mana_value: float,
    types: tuple[str, ...],
) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=colors,
        mana_value=mana_value,
        rarity="common",
        types=types,
    )


def _structure_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows.extend(
        _structure_deck_rows(
            draft_id="draft-a",
            wins="7",
            creature_count=16,
            spell_count=23,
        )
    )
    rows.extend(
        _structure_deck_rows(
            draft_id="draft-b",
            wins="7",
            creature_count=14,
            spell_count=24,
        )
    )
    rows.extend(
        _structure_deck_rows(
            draft_id="draft-c",
            wins="6",
            creature_count=16,
            spell_count=23,
        )
    )
    return rows


def _structure_deck_rows(
    *,
    draft_id: str,
    wins: str,
    creature_count: int,
    spell_count: int,
) -> list[dict[str, str]]:
    creature_names = [f"Structure Creature {index % 16}" for index in range(creature_count)]
    spell_names = [
        f"Structure Spell {index % 8}"
        for index in range(spell_count - creature_count)
    ]
    return [
        {
            "draft_id": draft_id,
            "expansion": "TST",
            "event_type": QUICK_DRAFT_FORMAT,
            "event_match_wins": wins,
            "pick": name,
            "pick_maindeck_rate": "1.0",
        }
        for name in (*creature_names, *spell_names)
    ]


def _pair_card_rating_row() -> dict[str, object]:
    return {
        "name": "Fixture Pair Filtered Card",
        "mtga_id": 1002,
        "color": "U",
        "rarity": "uncommon",
        "avg_seen": 3.25,
        "seen_count": 900,
        "pick_count": 600,
        "game_count": 700,
        "opening_hand_game_count": 180,
        "opening_hand_win_rate": 0.64,
        "ever_drawn_game_count": 650,
        "ever_drawn_win_rate": 0.66,
        "drawn_improvement_win_rate": 0.04,
    }
