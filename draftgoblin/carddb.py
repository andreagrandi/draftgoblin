"""Local card metadata cache backed by Scryfall bulk data.
Translate Arena grpIds into display-ready card facts for replay and scoring.
"""

from __future__ import annotations

import gzip
import io
import json
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any, TypeAlias

from draftgoblin import __version__
from draftgoblin.paths import app_data_dir

PathInput: TypeAlias = str | PathLike[str]

CARD_DATABASE_CACHE_FILENAME = "carddb.json"
SCRYFALL_BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
SCRYFALL_DEFAULT_CARDS_TYPE = "default_cards"
SCRYFALL_USER_AGENT = (
    f"draftgoblin/{__version__} "
    "(+https://github.com/andreagrandi/draftgoblin)"
)
HTTP_TIMEOUT_SECONDS = 60
COLOR_ORDER = ("W", "U", "B", "R", "G")
CACHE_SCHEMA_VERSION = 1


class CardDatabaseError(RuntimeError):
    """Base error for card database load, refresh, and parse failures.
    Callers can catch this to show concise CLI diagnostics.
    """


class CardDatabaseCacheMissingError(CardDatabaseError):
    """Raised when the card database cache has not been built yet.
    Run refresh-data once before relying on fully offline lookups.
    """


@dataclass(frozen=True, slots=True)
class CardInfo:
    """Display metadata for one Arena card id.
    Unknown markers use the same shape so callers never crash on misses.
    """

    grp_id: int
    name: str
    colors: tuple[str, ...]
    mana_value: float | None
    rarity: str
    types: tuple[str, ...]
    mana_cost: str | None = None
    produced_mana: tuple[str, ...] = ()
    unknown: bool = False

    @classmethod
    def unknown_card(cls, *, grp_id: int) -> CardInfo:
        """Build an explicit unknown-card marker.
        This keeps UI and replay paths total over arbitrary grpIds.
        """

        return cls(
            grp_id=grp_id,
            name=f"Unknown card {grp_id}",
            colors=(),
            mana_value=None,
            rarity="unknown",
            types=("Unknown",),
            mana_cost=None,
            produced_mana=(),
            unknown=True,
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> CardInfo:
        """Load a card entry from Draftgoblin's cache format.
        Cache parsing is strict so corrupted files fail loudly.
        """

        return cls(
            grp_id=_required_int(data.get("grp_id"), field_name="card.grp_id"),
            name=_required_str(data.get("name"), field_name="card.name"),
            colors=_string_tuple(data.get("colors"), field_name="card.colors"),
            mana_value=_optional_float(
                data.get("mana_value"),
                field_name="card.mana_value",
            ),
            rarity=_required_str(data.get("rarity"), field_name="card.rarity"),
            types=_string_tuple(data.get("types"), field_name="card.types"),
            mana_cost=_optional_str(data.get("mana_cost"), field_name="card.mana_cost"),
            produced_mana=_string_tuple(
                data.get("produced_mana", ()),
                field_name="card.produced_mana",
            ),
            unknown=bool(data.get("unknown", False)),
        )

    def to_json(self) -> dict[str, object]:
        """Convert this card entry to Draftgoblin's cache format.
        The result intentionally stores only the fields the app needs.
        """

        return {
            "grp_id": self.grp_id,
            "name": self.name,
            "colors": list(self.colors),
            "mana_value": self.mana_value,
            "rarity": self.rarity,
            "types": list(self.types),
            "mana_cost": self.mana_cost,
            "produced_mana": list(self.produced_mana),
            "unknown": self.unknown,
        }


@dataclass(frozen=True, slots=True)
class CardDatabase:
    """Lookup table from Arena grpId to card metadata.
    Missing grpIds return explicit unknown markers instead of raising.
    """

    cards: dict[int, CardInfo]

    def __len__(self) -> int:
        return len(self.cards)

    def lookup(self, *, grp_id: int) -> CardInfo:
        """Return card metadata or an explicit unknown marker.
        Lookup never raises for absent ids.
        """

        return self.cards.get(grp_id, CardInfo.unknown_card(grp_id=grp_id))

    def to_json(self) -> dict[str, object]:
        """Convert the database to Draftgoblin's cache format.
        Cards are sorted by grpId for stable cache diffs.
        """

        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source": "scryfall-default-cards",
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "cards": {
                str(grp_id): card.to_json()
                for grp_id, card in sorted(self.cards.items())
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> CardDatabase:
        """Load a card database from Draftgoblin's cache format.
        Cache schema mismatches fail before any partial lookup is used.
        """

        schema_version = _required_int(
            data.get("schema_version"),
            field_name="schema_version",
        )
        if schema_version != CACHE_SCHEMA_VERSION:
            raise CardDatabaseError(
                "Unsupported card database cache schema "
                f"{schema_version}; expected {CACHE_SCHEMA_VERSION}."
            )

        cards_value = data.get("cards")
        if not isinstance(cards_value, dict):
            raise CardDatabaseError("Card database cache is missing cards object.")

        cards: dict[int, CardInfo] = {}
        for key, value in cards_value.items():
            grp_id = _required_int(key, field_name="cards key")
            if not isinstance(value, dict):
                raise CardDatabaseError(f"Card cache entry {key!r} is not an object.")

            card = CardInfo.from_json(data=value)
            if card.grp_id != grp_id:
                raise CardDatabaseError(
                    f"Card cache key {grp_id} does not match entry grp_id {card.grp_id}."
                )

            cards[grp_id] = card

        return cls(cards=cards)


def card_database_cache_path(*, app_dir: PathInput | None = None) -> Path:
    """Return the default on-disk card database cache path.
    The parent directory is not created until a refresh writes the cache.
    """

    root = Path(app_data_dir() if app_dir is None else app_dir)
    return root / CARD_DATABASE_CACHE_FILENAME


def load_card_database(
    *,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
) -> CardDatabase:
    """Load the card database cache without making network calls.
    This is the fully offline path used after refresh-data has run once.
    """

    path = _cache_path(app_dir=app_dir, cache_path=cache_path)
    if not path.exists():
        raise CardDatabaseCacheMissingError(
            f"Card database cache does not exist at {path}. Run refresh-data first."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CardDatabaseError(f"Malformed card database cache {path}: {error}") from error

    if not isinstance(data, dict):
        raise CardDatabaseError(f"Malformed card database cache {path}: expected object.")

    return CardDatabase.from_json(data=data)


def save_card_database(
    database: CardDatabase,
    *,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
) -> Path:
    """Write a card database cache atomically.
    The destination parent directory is created if needed.
    """

    path = _cache_path(app_dir=app_dir, cache_path=cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(database.to_json(), indent=2, sort_keys=True)
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


def refresh_card_database(
    *,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
    bulk_file: PathInput | None = None,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> CardDatabase:
    """Build and cache the grpId map from Scryfall bulk data.
    Passing bulk_file keeps tests and local fixtures completely offline.
    """

    if bulk_file is None:
        database = download_scryfall_card_database(timeout_seconds=timeout_seconds)
    else:
        database = build_card_database_from_bulk_file(path=bulk_file)

    save_card_database(database, app_dir=app_dir, cache_path=cache_path)
    return database


def load_or_refresh_card_database(
    *,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
    refresh: bool = False,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> CardDatabase:
    """Load cached card data, refreshing only when explicitly requested.
    A missing cache triggers the same refresh path used by refresh-data.
    """

    if refresh:
        return refresh_card_database(
            app_dir=app_dir,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
        )

    try:
        return load_card_database(app_dir=app_dir, cache_path=cache_path)
    except CardDatabaseCacheMissingError:
        return refresh_card_database(
            app_dir=app_dir,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
        )


def download_scryfall_card_database(
    *,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> CardDatabase:
    """Download Scryfall's default-cards bulk file and build a database.
    Scryfall does not require an API key, only normal API headers.
    """

    bulk_items = _fetch_bulk_data_items(timeout_seconds=timeout_seconds)
    download_uri = _default_cards_download_uri(bulk_items=bulk_items)
    return build_card_database_from_scryfall_cards(
        cards=_iter_scryfall_jsonl_url(
            url=download_uri,
            timeout_seconds=timeout_seconds,
        )
    )


def build_card_database_from_bulk_file(*, path: PathInput) -> CardDatabase:
    """Build the card database from a local Scryfall JSONL file.
    Both plain .jsonl and Scryfall-style .jsonl.gz files are supported.
    """

    bulk_path = Path(path)
    with _open_text_bulk_file(path=bulk_path) as bulk_file:
        return build_card_database_from_scryfall_cards(
            cards=_iter_jsonl_objects(lines=bulk_file, source=str(bulk_path))
        )


def build_card_database_from_scryfall_cards(
    *,
    cards: Iterable[Mapping[str, Any]],
) -> CardDatabase:
    """Build the grpId map from Scryfall card objects.
    Cards without arena_id are intentionally ignored.
    """

    database_cards: dict[int, CardInfo] = {}
    for card_object in cards:
        card = _card_info_from_scryfall(card=card_object)
        if card is None:
            continue

        database_cards[card.grp_id] = card

    return CardDatabase(cards=database_cards)


def _cache_path(
    *,
    app_dir: PathInput | None,
    cache_path: PathInput | None,
) -> Path:
    if cache_path is not None:
        return Path(cache_path)

    return card_database_cache_path(app_dir=app_dir)


def _card_info_from_scryfall(*, card: Mapping[str, Any]) -> CardInfo | None:
    arena_id = card.get("arena_id")
    if arena_id is None:
        return None

    grp_id = _required_int(arena_id, field_name="arena_id")
    name = _required_str(card.get("name"), field_name=f"card {grp_id}.name")
    mana_value = _required_float(card.get("cmc"), field_name=f"card {grp_id}.cmc")
    rarity = _required_str(card.get("rarity"), field_name=f"card {grp_id}.rarity")
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=_card_colors(card=card, grp_id=grp_id),
        mana_value=mana_value,
        rarity=rarity,
        types=_card_types(card=card, grp_id=grp_id),
        mana_cost=_card_mana_cost(card=card, grp_id=grp_id),
        produced_mana=_card_produced_mana(card=card, grp_id=grp_id),
    )


def _card_colors(*, card: Mapping[str, Any], grp_id: int) -> tuple[str, ...]:
    colors_value = card.get("colors")
    if colors_value is not None:
        return _color_tuple(colors_value, field_name=f"card {grp_id}.colors")

    face_colors: list[str] = []
    faces_value = card.get("card_faces")
    if isinstance(faces_value, list):
        for face in faces_value:
            if not isinstance(face, dict):
                continue

            face_colors.extend(
                _color_tuple(
                    face.get("colors", ()),
                    field_name=f"card {grp_id}.card_faces[].colors",
                )
            )

    return _ordered_unique_colors(colors=face_colors)


def _card_types(*, card: Mapping[str, Any], grp_id: int) -> tuple[str, ...]:
    type_line_value = card.get("type_line")
    if isinstance(type_line_value, str) and type_line_value:
        return (type_line_value,)

    faces_value = card.get("card_faces")
    face_types: list[str] = []
    if isinstance(faces_value, list):
        for face in faces_value:
            if not isinstance(face, dict):
                continue

            face_type = face.get("type_line")
            if isinstance(face_type, str) and face_type:
                face_types.append(face_type)

    if face_types:
        return tuple(face_types)

    raise CardDatabaseError(f"Scryfall card {grp_id} is missing type_line.")


def _card_mana_cost(*, card: Mapping[str, Any], grp_id: int) -> str | None:
    mana_cost_value = card.get("mana_cost")
    if isinstance(mana_cost_value, str) and mana_cost_value:
        return mana_cost_value

    faces_value = card.get("card_faces")
    face_costs: list[str] = []
    if isinstance(faces_value, list):
        for face in faces_value:
            if not isinstance(face, dict):
                continue

            face_cost = face.get("mana_cost")
            if isinstance(face_cost, str) and face_cost:
                face_costs.append(face_cost)

    if face_costs:
        return " // ".join(face_costs)

    return None


def _card_produced_mana(*, card: Mapping[str, Any], grp_id: int) -> tuple[str, ...]:
    produced_value = card.get("produced_mana")
    if produced_value is not None:
        return _produced_mana_tuple(
            produced_value,
            field_name=f"card {grp_id}.produced_mana",
        )

    faces_value = card.get("card_faces")
    face_mana: list[str] = []
    if isinstance(faces_value, list):
        for face in faces_value:
            if not isinstance(face, dict):
                continue

            face_value = face.get("produced_mana")
            if face_value is None:
                continue

            face_mana.extend(
                _produced_mana_tuple(
                    face_value,
                    field_name=f"card {grp_id}.card_faces[].produced_mana",
                )
            )

    return _ordered_unique_colors(colors=face_mana)


def _open_text_bulk_file(*, path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8")

    return path.open(mode="rt", encoding="utf-8")


def _iter_scryfall_jsonl_url(
    *,
    url: str,
    timeout_seconds: int,
) -> Iterator[Mapping[str, Any]]:
    request = _request(url=url)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            with gzip.GzipFile(fileobj=response) as compressed:
                text_stream = io.TextIOWrapper(compressed, encoding="utf-8")
                yield from _iter_jsonl_objects(lines=text_stream, source=url)
    except urllib.error.URLError as error:
        raise CardDatabaseError(f"Failed to download Scryfall bulk data: {error}") from error
    except OSError as error:
        raise CardDatabaseError(f"Failed to decompress Scryfall bulk data: {error}") from error


def _iter_jsonl_objects(
    *,
    lines: Iterable[str],
    source: str,
) -> Iterator[Mapping[str, Any]]:
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise CardDatabaseError(
                f"Malformed Scryfall bulk JSON at {source}:{line_number}: {error.msg}."
            ) from error

        if not isinstance(item, dict):
            raise CardDatabaseError(
                f"Malformed Scryfall bulk JSON at {source}:{line_number}: "
                "expected object."
            )

        yield item


def _fetch_bulk_data_items(*, timeout_seconds: int) -> list[Mapping[str, Any]]:
    request = _request(url=SCRYFALL_BULK_DATA_URL)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise CardDatabaseError(f"Failed to query Scryfall bulk metadata: {error}") from error
    except json.JSONDecodeError as error:
        raise CardDatabaseError(f"Malformed Scryfall bulk metadata: {error}") from error

    if not isinstance(payload, dict):
        raise CardDatabaseError("Malformed Scryfall bulk metadata: expected object.")

    data = payload.get("data")
    if not isinstance(data, list):
        raise CardDatabaseError("Malformed Scryfall bulk metadata: missing data list.")

    items: list[Mapping[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise CardDatabaseError("Malformed Scryfall bulk metadata: item is not object.")

        items.append(item)

    return items


def _default_cards_download_uri(*, bulk_items: Iterable[Mapping[str, Any]]) -> str:
    for item in bulk_items:
        if item.get("type") != SCRYFALL_DEFAULT_CARDS_TYPE:
            continue

        uri = item.get("jsonl_download_uri", item.get("download_uri"))
        return _required_str(uri, field_name="default_cards.download_uri")

    raise CardDatabaseError("Scryfall bulk metadata did not include default_cards.")


def _request(*, url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/json;q=0.9,*/*;q=0.8",
            "User-Agent": SCRYFALL_USER_AGENT,
        },
    )


def _color_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    colors = _string_tuple(value, field_name=field_name)
    invalid = [color for color in colors if color not in COLOR_ORDER]
    if invalid:
        raise CardDatabaseError(f"Invalid color values in {field_name}: {invalid}.")

    return _ordered_unique_colors(colors=colors)


def _produced_mana_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    mana_symbols = _string_tuple(value, field_name=field_name)
    invalid = [symbol for symbol in mana_symbols if symbol not in (*COLOR_ORDER, "C")]
    if invalid:
        raise CardDatabaseError(f"Invalid mana values in {field_name}: {invalid}.")

    return _ordered_unique_colors(
        colors=(symbol for symbol in mana_symbols if symbol in COLOR_ORDER)
    )


def _ordered_unique_colors(*, colors: Iterable[str]) -> tuple[str, ...]:
    color_set = set(colors)
    return tuple(color for color in COLOR_ORDER if color in color_set)


def _string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise CardDatabaseError(f"Missing or invalid {field_name}; expected string list.")

    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise CardDatabaseError(
                f"Missing or invalid {field_name}; expected only strings."
            )

        result.append(item)

    return tuple(result)


def _required_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise CardDatabaseError(
            f"Missing or invalid {field_name}; expected non-empty string."
        )

    return value


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None

    return _required_str(value, field_name=field_name)


def _required_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise CardDatabaseError(f"Missing or invalid {field_name}; expected integer.")

    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise CardDatabaseError(
            f"Missing or invalid {field_name}; expected integer."
        ) from error


def _required_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise CardDatabaseError(f"Missing or invalid {field_name}; expected number.")

    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise CardDatabaseError(
            f"Missing or invalid {field_name}; expected number."
        ) from error


def _optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None

    return _required_float(value, field_name=field_name)

