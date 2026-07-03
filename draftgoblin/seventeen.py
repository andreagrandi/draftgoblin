"""17Lands ratings fetch, cache, and fallback handling.
Keep network access isolated so CI can exercise recorded responses only.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from os import PathLike
from pathlib import Path
from typing import Any, TypeAlias

from draftgoblin import __version__
from draftgoblin.config import COLOR_PAIRS, PICK_ENGINE, RATINGS_CACHE_TTL_HOURS
from draftgoblin.paths import app_data_dir

PathInput: TypeAlias = str | PathLike[str]
Clock: TypeAlias = Callable[[], datetime]
FetchJson: TypeAlias = Callable[[str, int], Any]

SEVENTEEN_LANDS_ATTRIBUTION = "Card data from 17Lands (17lands.com)"
SEVENTEEN_LANDS_BASE_URL = "https://www.17lands.com"
CARD_RATINGS_ENDPOINT = f"{SEVENTEEN_LANDS_BASE_URL}/card_ratings/data"
COLOR_RATINGS_ENDPOINT = f"{SEVENTEEN_LANDS_BASE_URL}/color_ratings/data"
SEVENTEEN_LANDS_USER_AGENT = (
    f"draftgoblin/{__version__} "
    "(+https://github.com/andreagrandi/draftgoblin)"
)
QUICK_DRAFT_FORMAT = "QuickDraft"
PREMIER_DRAFT_FORMAT = "PremierDraft"
FORMAT_RATING_SOURCE = "format"
NEUTRAL_PRIOR_SOURCE = "neutral-prior"
CACHE_SCHEMA_VERSION = 1
SEVENTEEN_CACHE_DIRECTORY_NAME = "17lands"
HTTP_TIMEOUT_SECONDS = 60
ALL_TIME_START_DATE = date(year=2020, month=1, day=1)


class SeventeenLandsError(RuntimeError):
    """Base error for 17Lands load, refresh, and parse failures.
    Callers can surface this as a concise CLI diagnostic.
    """


class SeventeenLandsCacheMissingError(SeventeenLandsError):
    """Raised when a 17Lands cache has not been built yet.
    The auto-refresh path normally handles this before callers see it.
    """


@dataclass(frozen=True, slots=True)
class RatingSampleCounts:
    """Sample counts reported by 17Lands for one card row.
    These counts let callers distinguish strong signals from thin data.
    """

    seen: int
    picked: int
    games_played: int
    opening_hand: int
    games_in_hand: int

    def to_json(self) -> dict[str, int]:
        """Convert sample counts to Draftgoblin's cache shape.
        The field names stay close to the 17Lands metric names.
        """

        return {
            "seen": self.seen,
            "picked": self.picked,
            "games_played": self.games_played,
            "opening_hand": self.opening_hand,
            "games_in_hand": self.games_in_hand,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RatingSampleCounts:
        """Load sample counts from Draftgoblin's cache shape.
        Parsing is strict so corrupted caches fail loudly.
        """

        return cls(
            seen=_required_int(data.get("seen"), field_name="sample_counts.seen"),
            picked=_required_int(data.get("picked"), field_name="sample_counts.picked"),
            games_played=_required_int(
                data.get("games_played"),
                field_name="sample_counts.games_played",
            ),
            opening_hand=_required_int(
                data.get("opening_hand"),
                field_name="sample_counts.opening_hand",
            ),
            games_in_hand=_required_int(
                data.get("games_in_hand"),
                field_name="sample_counts.games_in_hand",
            ),
        )


@dataclass(frozen=True, slots=True)
class SeventeenCardStats:
    """One normalized card-rating row from 17Lands.
    Win rates are stored as fractions, matching the upstream JSON endpoint.
    """

    grp_id: int
    name: str
    color: str
    rarity: str
    average_last_seen_at: float | None
    gih_win_rate: float | None
    opening_hand_win_rate: float | None
    drawn_improvement_win_rate: float | None
    sample_counts: RatingSampleCounts

    def to_json(self) -> dict[str, object]:
        """Convert this rating to Draftgoblin's cache shape.
        Cards are keyed by grpId at the container level.
        """

        return {
            "grp_id": self.grp_id,
            "name": self.name,
            "color": self.color,
            "rarity": self.rarity,
            "average_last_seen_at": self.average_last_seen_at,
            "gih_win_rate": self.gih_win_rate,
            "opening_hand_win_rate": self.opening_hand_win_rate,
            "drawn_improvement_win_rate": self.drawn_improvement_win_rate,
            "sample_counts": self.sample_counts.to_json(),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SeventeenCardStats:
        """Load one card rating from Draftgoblin's cache shape.
        Schema mismatches fail before a partial rating table is used.
        """

        sample_counts_value = data.get("sample_counts")
        if not isinstance(sample_counts_value, dict):
            raise SeventeenLandsError(
                "17Lands card rating is missing sample_counts object."
            )

        return cls(
            grp_id=_required_int(data.get("grp_id"), field_name="card.grp_id"),
            name=_required_str(data.get("name"), field_name="card.name"),
            color=_required_str(data.get("color"), field_name="card.color"),
            rarity=_required_str(data.get("rarity"), field_name="card.rarity"),
            average_last_seen_at=_optional_float(
                data.get("average_last_seen_at"),
                field_name="card.average_last_seen_at",
            ),
            gih_win_rate=_optional_float(
                data.get("gih_win_rate"),
                field_name="card.gih_win_rate",
            ),
            opening_hand_win_rate=_optional_float(
                data.get("opening_hand_win_rate"),
                field_name="card.opening_hand_win_rate",
            ),
            drawn_improvement_win_rate=_optional_float(
                data.get("drawn_improvement_win_rate"),
                field_name="card.drawn_improvement_win_rate",
            ),
            sample_counts=RatingSampleCounts.from_json(data=sample_counts_value),
        )


@dataclass(frozen=True, slots=True)
class ColorPairWinRate:
    """Aggregate game record for one two-color pair.
    The win_rate field is None when 17Lands reports zero games.
    """

    pair: str
    wins: int
    games: int
    win_rate: float | None

    def to_json(self) -> dict[str, object]:
        """Convert this pair record to Draftgoblin's cache shape.
        Pair records are keyed by pair at the container level.
        """

        return {
            "pair": self.pair,
            "wins": self.wins,
            "games": self.games,
            "win_rate": self.win_rate,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ColorPairWinRate:
        """Load one pair record from Draftgoblin's cache shape.
        Pair codes are validated against the configured color-pair list.
        """

        pair = _required_pair(data.get("pair"), field_name="pair.pair")
        wins = _required_int(data.get("wins"), field_name="pair.wins")
        games = _required_int(data.get("games"), field_name="pair.games")
        win_rate = _optional_float(data.get("win_rate"), field_name="pair.win_rate")
        _ensure_non_negative(value=wins, field_name="pair.wins")
        _ensure_non_negative(value=games, field_name="pair.games")
        return cls(pair=pair, wins=wins, games=games, win_rate=win_rate)


@dataclass(frozen=True, slots=True)
class SeventeenLandsFormatData:
    """Cached 17Lands data for one set and event format.
    It contains both card ratings and two-color pair win rates.
    """

    set_code: str
    event_format: str
    fetched_at: datetime
    card_ratings: dict[int, SeventeenCardStats]
    pair_win_rates: dict[str, ColorPairWinRate]

    @property
    def attribution(self) -> str:
        """Return the required 17Lands attribution string.
        UI and build-sheet callers can display this verbatim.
        """

        return SEVENTEEN_LANDS_ATTRIBUTION

    def to_json(self) -> dict[str, object]:
        """Convert this dataset to Draftgoblin's cache shape.
        Keys are sorted so cache files remain stable and inspectable.
        """

        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source": "17lands",
            "set_code": self.set_code,
            "event_format": self.event_format,
            "fetched_at": self.fetched_at.astimezone(UTC).isoformat(),
            "card_ratings": {
                str(grp_id): card.to_json()
                for grp_id, card in sorted(self.card_ratings.items())
            },
            "pair_win_rates": {
                pair: win_rate.to_json()
                for pair, win_rate in sorted(self.pair_win_rates.items())
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SeventeenLandsFormatData:
        """Load a cached 17Lands dataset from Draftgoblin's JSON shape.
        Schema mismatches and corrupted entries fail loudly.
        """

        schema_version = _required_int(
            data.get("schema_version"),
            field_name="schema_version",
        )
        if schema_version != CACHE_SCHEMA_VERSION:
            raise SeventeenLandsError(
                "Unsupported 17Lands cache schema "
                f"{schema_version}; expected {CACHE_SCHEMA_VERSION}."
            )

        card_ratings_value = data.get("card_ratings")
        if not isinstance(card_ratings_value, dict):
            raise SeventeenLandsError("17Lands cache is missing card_ratings object.")

        pair_win_rates_value = data.get("pair_win_rates")
        if not isinstance(pair_win_rates_value, dict):
            raise SeventeenLandsError("17Lands cache is missing pair_win_rates object.")

        card_ratings: dict[int, SeventeenCardStats] = {}
        for key, value in card_ratings_value.items():
            grp_id = _required_int(key, field_name="card_ratings key")
            if not isinstance(value, dict):
                raise SeventeenLandsError(
                    f"17Lands card rating {key!r} is not an object."
                )

            card = SeventeenCardStats.from_json(data=value)
            if card.grp_id != grp_id:
                raise SeventeenLandsError(
                    f"17Lands card key {grp_id} does not match entry "
                    f"grp_id {card.grp_id}."
                )

            card_ratings[grp_id] = card

        pair_win_rates: dict[str, ColorPairWinRate] = {}
        for key, value in pair_win_rates_value.items():
            pair = _required_pair(key, field_name="pair_win_rates key")
            if not isinstance(value, dict):
                raise SeventeenLandsError(
                    f"17Lands pair win rate {key!r} is not an object."
                )

            win_rate = ColorPairWinRate.from_json(data=value)
            if win_rate.pair != pair:
                raise SeventeenLandsError(
                    f"17Lands pair key {pair} does not match entry pair "
                    f"{win_rate.pair}."
                )

            pair_win_rates[pair] = win_rate

        return cls(
            set_code=_required_str(data.get("set_code"), field_name="set_code"),
            event_format=_required_str(
                data.get("event_format"),
                field_name="event_format",
            ),
            fetched_at=_required_datetime(data.get("fetched_at"), field_name="fetched_at"),
            card_ratings=card_ratings,
            pair_win_rates=pair_win_rates,
        )


@dataclass(frozen=True, slots=True)
class RatingSourceMetadata:
    """Explain where a resolved card rating came from.
    Fallback metadata is explicit so UI callers can flag Premier or neutral data.
    """

    requested_format: str
    source: str
    source_format: str | None
    fallback_reason: str | None


@dataclass(frozen=True, slots=True)
class ResolvedCardRating:
    """A card rating after applying the fallback chain.
    Neutral-prior rows keep any useful ALSA-like context from cached data.
    """

    grp_id: int
    name: str
    color: str | None
    rarity: str | None
    average_last_seen_at: float | None
    gih_win_rate: float | None
    opening_hand_win_rate: float | None
    drawn_improvement_win_rate: float | None
    sample_counts: RatingSampleCounts
    neutral_prior_score: float | None
    metadata: RatingSourceMetadata

    @property
    def neutral_prior(self) -> bool:
        """Return whether this rating is the neutral-prior fallback.
        Callers can use this to downplay unavailable 17Lands signals.
        """

        return self.metadata.source == NEUTRAL_PRIOR_SOURCE


@dataclass(frozen=True, slots=True)
class SeventeenLandsData:
    """High-level 17Lands view with Quick→Premier→neutral fallback.
    Pair win rates come from the requested primary event format.
    """

    set_code: str
    requested_format: str
    primary: SeventeenLandsFormatData
    fallback: SeventeenLandsFormatData | None
    thin_sample_minimum: int = PICK_ENGINE.thin_sample_minimum
    attribution: str = SEVENTEEN_LANDS_ATTRIBUTION

    @property
    def pair_win_rates(self) -> dict[str, ColorPairWinRate]:
        """Return pair win rates for the requested primary format.
        The returned dictionary is the cached primary pair table.
        """

        return self.primary.pair_win_rates

    @property
    def ratings(self) -> dict[int, ResolvedCardRating]:
        """Return resolved ratings for cards seen in cached format data.
        Arbitrary absent grpIds can still be resolved through rating_for.
        """

        grp_ids = set(self.primary.card_ratings)
        if self.fallback is not None:
            grp_ids.update(self.fallback.card_ratings)

        return {
            grp_id: self.rating_for(grp_id=grp_id)
            for grp_id in sorted(grp_ids)
        }

    def rating_for(self, *, grp_id: int) -> ResolvedCardRating:
        """Resolve one grpId through Quick, Premier, then neutral prior.
        Missing and thin primary data are flagged in the returned metadata.
        """

        primary_stats = self.primary.card_ratings.get(grp_id)
        if _has_strong_gih_signal(
            stats=primary_stats,
            thin_sample_minimum=self.thin_sample_minimum,
        ):
            return _resolved_from_stats(
                stats=primary_stats,
                requested_format=self.requested_format,
                source_format=self.primary.event_format,
                fallback_reason=None,
            )

        fallback_reason = "primary-missing" if primary_stats is None else "primary-thin"
        fallback_stats = None
        if self.fallback is not None:
            fallback_stats = self.fallback.card_ratings.get(grp_id)

        if _has_strong_gih_signal(
            stats=fallback_stats,
            thin_sample_minimum=self.thin_sample_minimum,
        ):
            return _resolved_from_stats(
                stats=fallback_stats,
                requested_format=self.requested_format,
                source_format=self.fallback.event_format if self.fallback is not None else None,
                fallback_reason=fallback_reason,
            )

        neutral_stats = primary_stats or fallback_stats
        if neutral_stats is None:
            neutral_reason = "fallback-missing"
        elif fallback_stats is None and primary_stats is not None:
            neutral_reason = "fallback-missing"
        else:
            neutral_reason = "fallback-thin"

        return _neutral_rating(
            grp_id=grp_id,
            stats=neutral_stats,
            requested_format=self.requested_format,
            fallback_reason=neutral_reason,
        )


def seventeen_lands_cache_path(
    *,
    set_code: str,
    event_format: str,
    app_dir: PathInput | None = None,
) -> Path:
    """Return the on-disk cache path for one set and format.
    The parent directory is created only when a refresh writes the cache.
    """

    root = Path(app_data_dir() if app_dir is None else app_dir)
    filename = f"{_path_segment(set_code.upper())}-{_path_segment(event_format)}.json"
    return root / SEVENTEEN_CACHE_DIRECTORY_NAME / filename


def card_ratings_url(
    *,
    set_code: str,
    event_format: str,
    end_date: date,
) -> str:
    """Build the 17Lands card-ratings endpoint URL.
    A broad date range gives the same all-time table as the website filters.
    """

    return _url_with_query(
        endpoint=CARD_RATINGS_ENDPOINT,
        params={
            "expansion": set_code.upper(),
            "event_type": event_format,
            "start_date": ALL_TIME_START_DATE.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )


def color_ratings_url(
    *,
    set_code: str,
    event_format: str,
    end_date: date,
) -> str:
    """Build the 17Lands color-ratings endpoint URL.
    Splashes are not combined so exact two-color pair rows are preserved.
    """

    return _url_with_query(
        endpoint=COLOR_RATINGS_ENDPOINT,
        params={
            "expansion": set_code.upper(),
            "event_type": event_format,
            "start_date": ALL_TIME_START_DATE.isoformat(),
            "end_date": end_date.isoformat(),
            "combine_splash": "false",
        },
    )


def load_or_refresh_17lands_data(
    *,
    set_code: str,
    event_format: str = QUICK_DRAFT_FORMAT,
    app_dir: PathInput | None = None,
    refresh: bool = False,
    clock: Clock | None = None,
    fetch_json: FetchJson | None = None,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
    cache_ttl: timedelta | None = None,
    thin_sample_minimum: int = PICK_ENGINE.thin_sample_minimum,
    premier_fallback_enabled: bool = PICK_ENGINE.premier_fallback_enabled,
) -> SeventeenLandsData:
    """Load 17Lands data with the configured fallback chain.
    Premier fallback failures degrade to neutral-prior ratings.
    """

    primary = load_or_refresh_17lands_format_data(
        set_code=set_code,
        event_format=event_format,
        app_dir=app_dir,
        refresh=refresh,
        clock=clock,
        fetch_json=fetch_json,
        timeout_seconds=timeout_seconds,
        cache_ttl=cache_ttl,
    )
    fallback = None
    if premier_fallback_enabled and event_format != PREMIER_DRAFT_FORMAT:
        try:
            fallback = load_or_refresh_17lands_format_data(
                set_code=set_code,
                event_format=PREMIER_DRAFT_FORMAT,
                app_dir=app_dir,
                refresh=refresh,
                clock=clock,
                fetch_json=fetch_json,
                timeout_seconds=timeout_seconds,
                cache_ttl=cache_ttl,
            )
        except SeventeenLandsError:
            fallback = None

    return SeventeenLandsData(
        set_code=set_code.upper(),
        requested_format=event_format,
        primary=primary,
        fallback=fallback,
        thin_sample_minimum=thin_sample_minimum,
    )


def load_or_refresh_17lands_format_data(
    *,
    set_code: str,
    event_format: str,
    app_dir: PathInput | None = None,
    refresh: bool = False,
    clock: Clock | None = None,
    fetch_json: FetchJson | None = None,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
    cache_ttl: timedelta | None = None,
) -> SeventeenLandsFormatData:
    """Load cached format data, refreshing only when stale.
    Stale but valid cache data is served when a refresh fails offline.
    """

    now = _now(clock=clock)
    ttl = _cache_ttl(cache_ttl=cache_ttl)
    cached = _load_existing_format_data(
        set_code=set_code,
        event_format=event_format,
        app_dir=app_dir,
    )
    if cached is not None and not refresh and not _is_stale(
        fetched_at=cached.fetched_at,
        now=now,
        cache_ttl=ttl,
    ):
        return cached

    try:
        return refresh_17lands_format_data(
            set_code=set_code,
            event_format=event_format,
            app_dir=app_dir,
            fetched_at=now,
            fetch_json=fetch_json,
            timeout_seconds=timeout_seconds,
        )
    except SeventeenLandsError:
        if cached is not None and not refresh:
            return cached

        raise


def load_17lands_format_data(
    *,
    set_code: str,
    event_format: str,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
) -> SeventeenLandsFormatData:
    """Load one cached 17Lands set/format dataset without network calls.
    This is the fully offline path used after a successful refresh.
    """

    path = _format_cache_path(
        set_code=set_code,
        event_format=event_format,
        app_dir=app_dir,
        cache_path=cache_path,
    )
    if not path.exists():
        raise SeventeenLandsCacheMissingError(
            f"17Lands cache does not exist at {path}. Refresh ratings first."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SeventeenLandsError(f"Malformed 17Lands cache {path}: {error}") from error

    if not isinstance(data, dict):
        raise SeventeenLandsError(f"Malformed 17Lands cache {path}: expected object.")

    dataset = SeventeenLandsFormatData.from_json(data=data)
    if dataset.set_code != set_code.upper():
        raise SeventeenLandsError(
            f"17Lands cache {path} is for set {dataset.set_code}, not {set_code.upper()}."
        )

    if dataset.event_format != event_format:
        raise SeventeenLandsError(
            f"17Lands cache {path} is for format {dataset.event_format}, "
            f"not {event_format}."
        )

    return dataset


def save_17lands_format_data(
    dataset: SeventeenLandsFormatData,
    *,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
) -> Path:
    """Write a 17Lands dataset cache atomically.
    The destination parent directory is created if needed.
    """

    path = _format_cache_path(
        set_code=dataset.set_code,
        event_format=dataset.event_format,
        app_dir=app_dir,
        cache_path=cache_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dataset.to_json(), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=path.parent,
        encoding="utf-8",
    ) as temporary_file:
        temporary_file.write(payload)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)

    temporary_path.replace(path)
    return path


def refresh_17lands_format_data(
    *,
    set_code: str,
    event_format: str,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
    fetched_at: datetime | None = None,
    fetch_json: FetchJson | None = None,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> SeventeenLandsFormatData:
    """Fetch 17Lands endpoints and write the normalized cache.
    Tests pass a fetch_json callable so no live network is required.
    """

    timestamp = datetime.now(tz=UTC) if fetched_at is None else fetched_at.astimezone(UTC)
    dataset = fetch_17lands_format_data(
        set_code=set_code,
        event_format=event_format,
        fetched_at=timestamp,
        fetch_json=fetch_json,
        timeout_seconds=timeout_seconds,
    )
    save_17lands_format_data(dataset, app_dir=app_dir, cache_path=cache_path)
    return dataset


def fetch_17lands_format_data(
    *,
    set_code: str,
    event_format: str,
    fetched_at: datetime | None = None,
    fetch_json: FetchJson | None = None,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> SeventeenLandsFormatData:
    """Fetch and normalize one set/format from 17Lands.
    The fetch_json hook receives each URL and timeout in seconds.
    """

    timestamp = datetime.now(tz=UTC) if fetched_at is None else fetched_at.astimezone(UTC)
    end_date = timestamp.date()
    json_fetcher = _default_fetch_json if fetch_json is None else fetch_json
    card_payload = json_fetcher(
        card_ratings_url(
            set_code=set_code,
            event_format=event_format,
            end_date=end_date,
        ),
        timeout_seconds,
    )
    color_payload = json_fetcher(
        color_ratings_url(
            set_code=set_code,
            event_format=event_format,
            end_date=end_date,
        ),
        timeout_seconds,
    )
    return build_17lands_format_data(
        set_code=set_code,
        event_format=event_format,
        fetched_at=timestamp,
        card_ratings_payload=card_payload,
        color_ratings_payload=color_payload,
    )


def build_17lands_format_data(
    *,
    set_code: str,
    event_format: str,
    fetched_at: datetime,
    card_ratings_payload: Any,
    color_ratings_payload: Any,
) -> SeventeenLandsFormatData:
    """Normalize recorded 17Lands endpoint responses into cache data.
    This powers tests and the live refresh path with identical parsing.
    """

    return SeventeenLandsFormatData(
        set_code=set_code.upper(),
        event_format=event_format,
        fetched_at=fetched_at.astimezone(UTC),
        card_ratings=_parse_card_ratings(payload=card_ratings_payload),
        pair_win_rates=_parse_pair_win_rates(payload=color_ratings_payload),
    )


def _load_existing_format_data(
    *,
    set_code: str,
    event_format: str,
    app_dir: PathInput | None,
) -> SeventeenLandsFormatData | None:
    try:
        return load_17lands_format_data(
            set_code=set_code,
            event_format=event_format,
            app_dir=app_dir,
        )
    except SeventeenLandsCacheMissingError:
        return None


def _parse_card_ratings(*, payload: Any) -> dict[int, SeventeenCardStats]:
    if not isinstance(payload, list):
        raise SeventeenLandsError("17Lands card ratings payload is not a list.")

    ratings: dict[int, SeventeenCardStats] = {}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise SeventeenLandsError(
                f"17Lands card ratings item {index} is not an object."
            )

        stats = _card_stats_from_endpoint_row(row=item, index=index)
        ratings[stats.grp_id] = stats

    return ratings


def _card_stats_from_endpoint_row(
    *,
    row: Mapping[str, Any],
    index: int,
) -> SeventeenCardStats:
    grp_id = _required_int(row.get("mtga_id"), field_name=f"card_ratings[{index}].mtga_id")
    return SeventeenCardStats(
        grp_id=grp_id,
        name=_required_str(row.get("name"), field_name=f"card {grp_id}.name"),
        color=_optional_str(row.get("color"), field_name=f"card {grp_id}.color") or "C",
        rarity=_required_str(row.get("rarity"), field_name=f"card {grp_id}.rarity"),
        average_last_seen_at=_optional_float(
            row.get("avg_seen"),
            field_name=f"card {grp_id}.avg_seen",
        ),
        gih_win_rate=_optional_float(
            row.get("ever_drawn_win_rate"),
            field_name=f"card {grp_id}.ever_drawn_win_rate",
        ),
        opening_hand_win_rate=_optional_float(
            row.get("opening_hand_win_rate"),
            field_name=f"card {grp_id}.opening_hand_win_rate",
        ),
        drawn_improvement_win_rate=_optional_float(
            row.get("drawn_improvement_win_rate"),
            field_name=f"card {grp_id}.drawn_improvement_win_rate",
        ),
        sample_counts=RatingSampleCounts(
            seen=_optional_int(row.get("seen_count"), field_name=f"card {grp_id}.seen_count")
            or 0,
            picked=_optional_int(
                row.get("pick_count"),
                field_name=f"card {grp_id}.pick_count",
            )
            or 0,
            games_played=_optional_int(
                row.get("game_count"),
                field_name=f"card {grp_id}.game_count",
            )
            or 0,
            opening_hand=_optional_int(
                row.get("opening_hand_game_count"),
                field_name=f"card {grp_id}.opening_hand_game_count",
            )
            or 0,
            games_in_hand=_optional_int(
                row.get("ever_drawn_game_count"),
                field_name=f"card {grp_id}.ever_drawn_game_count",
            )
            or 0,
        ),
    )


def _parse_pair_win_rates(*, payload: Any) -> dict[str, ColorPairWinRate]:
    if not isinstance(payload, list):
        raise SeventeenLandsError("17Lands color ratings payload is not a list.")

    pairs: dict[str, ColorPairWinRate] = {}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise SeventeenLandsError(
                f"17Lands color ratings item {index} is not an object."
            )

        pair = item.get("short_name")
        if pair not in COLOR_PAIRS:
            continue

        wins = _required_int(item.get("wins"), field_name=f"color_ratings[{index}].wins")
        games = _required_int(item.get("games"), field_name=f"color_ratings[{index}].games")
        _ensure_non_negative(value=wins, field_name=f"color_ratings[{index}].wins")
        _ensure_non_negative(value=games, field_name=f"color_ratings[{index}].games")
        pairs[pair] = ColorPairWinRate(
            pair=pair,
            wins=wins,
            games=games,
            win_rate=(wins / games) if games else None,
        )

    return pairs


def _resolved_from_stats(
    *,
    stats: SeventeenCardStats | None,
    requested_format: str,
    source_format: str | None,
    fallback_reason: str | None,
) -> ResolvedCardRating:
    if stats is None:
        raise SeventeenLandsError("Cannot resolve a rating from missing card stats.")

    return ResolvedCardRating(
        grp_id=stats.grp_id,
        name=stats.name,
        color=stats.color,
        rarity=stats.rarity,
        average_last_seen_at=stats.average_last_seen_at,
        gih_win_rate=stats.gih_win_rate,
        opening_hand_win_rate=stats.opening_hand_win_rate,
        drawn_improvement_win_rate=stats.drawn_improvement_win_rate,
        sample_counts=stats.sample_counts,
        neutral_prior_score=None,
        metadata=RatingSourceMetadata(
            requested_format=requested_format,
            source=FORMAT_RATING_SOURCE,
            source_format=source_format,
            fallback_reason=fallback_reason,
        ),
    )


def _neutral_rating(
    *,
    grp_id: int,
    stats: SeventeenCardStats | None,
    requested_format: str,
    fallback_reason: str,
) -> ResolvedCardRating:
    sample_counts = (
        stats.sample_counts
        if stats is not None
        else RatingSampleCounts(
            seen=0,
            picked=0,
            games_played=0,
            opening_hand=0,
            games_in_hand=0,
        )
    )
    return ResolvedCardRating(
        grp_id=grp_id,
        name=stats.name if stats is not None else f"Unknown card {grp_id}",
        color=stats.color if stats is not None else None,
        rarity=stats.rarity if stats is not None else None,
        average_last_seen_at=stats.average_last_seen_at if stats is not None else None,
        gih_win_rate=None,
        opening_hand_win_rate=stats.opening_hand_win_rate if stats is not None else None,
        drawn_improvement_win_rate=(
            stats.drawn_improvement_win_rate if stats is not None else None
        ),
        sample_counts=sample_counts,
        neutral_prior_score=PICK_ENGINE.neutral_prior_score,
        metadata=RatingSourceMetadata(
            requested_format=requested_format,
            source=NEUTRAL_PRIOR_SOURCE,
            source_format=None,
            fallback_reason=fallback_reason,
        ),
    )


def _has_strong_gih_signal(
    *,
    stats: SeventeenCardStats | None,
    thin_sample_minimum: int,
) -> bool:
    if stats is None:
        return False

    return (
        stats.gih_win_rate is not None
        and stats.sample_counts.games_in_hand >= thin_sample_minimum
    )


def _default_fetch_json(url: str, timeout_seconds: int) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json;q=0.9,*/*;q=0.8",
            "User-Agent": SEVENTEEN_LANDS_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise SeventeenLandsError(f"Failed to query 17Lands data: {error}") from error
    except json.JSONDecodeError as error:
        raise SeventeenLandsError(f"Malformed 17Lands JSON response: {error}") from error


def _url_with_query(*, endpoint: str, params: Mapping[str, str]) -> str:
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def _format_cache_path(
    *,
    set_code: str,
    event_format: str,
    app_dir: PathInput | None,
    cache_path: PathInput | None,
) -> Path:
    if cache_path is not None:
        return Path(cache_path)

    return seventeen_lands_cache_path(
        set_code=set_code,
        event_format=event_format,
        app_dir=app_dir,
    )


def _is_stale(*, fetched_at: datetime, now: datetime, cache_ttl: timedelta) -> bool:
    return now.astimezone(UTC) - fetched_at.astimezone(UTC) >= cache_ttl


def _cache_ttl(*, cache_ttl: timedelta | None) -> timedelta:
    if cache_ttl is not None:
        return cache_ttl

    return timedelta(hours=RATINGS_CACHE_TTL_HOURS)


def _now(*, clock: Clock | None) -> datetime:
    if clock is None:
        return datetime.now(tz=UTC)

    return clock().astimezone(UTC)


def _required_datetime(value: Any, *, field_name: str) -> datetime:
    text = _required_str(value, field_name=field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise SeventeenLandsError(
            f"Missing or invalid {field_name}; expected ISO datetime."
        ) from error

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def _required_pair(value: Any, *, field_name: str) -> str:
    pair = _required_str(value, field_name=field_name)
    if pair not in COLOR_PAIRS:
        raise SeventeenLandsError(
            f"Missing or invalid {field_name}; expected a two-color pair."
        )

    return pair


def _path_segment(value: str) -> str:
    if value in {"", ".", ".."}:
        raise SeventeenLandsError("Invalid 17Lands cache path segment.")

    return value.replace("/", "_").replace("\\", "_").replace(":", "_")


def _ensure_non_negative(*, value: int, field_name: str) -> None:
    if value < 0:
        raise SeventeenLandsError(f"Missing or invalid {field_name}; expected >= 0.")


def _required_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise SeventeenLandsError(
            f"Missing or invalid {field_name}; expected non-empty string."
        )

    return value


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None

    return _required_str(value, field_name=field_name)


def _required_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise SeventeenLandsError(f"Missing or invalid {field_name}; expected integer.")

    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise SeventeenLandsError(
            f"Missing or invalid {field_name}; expected integer."
        ) from error


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None

    return _required_int(value, field_name=field_name)


def _required_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise SeventeenLandsError(f"Missing or invalid {field_name}; expected number.")

    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise SeventeenLandsError(
            f"Missing or invalid {field_name}; expected number."
        ) from error


def _optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None

    return _required_float(value, field_name=field_name)
