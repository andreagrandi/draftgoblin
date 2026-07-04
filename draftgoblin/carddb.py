"""Local card metadata cache backed by Scryfall and Arena data.
Translate Arena grpIds into display-ready card facts for replay and scoring.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import platform
import tempfile
import urllib.error
import urllib.parse
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
MTGJSON_SET_URL_TEMPLATE = "https://mtgjson.com/api/v5/{set_code}.json"
SCRYFALL_USER_AGENT = (
    f"draftgoblin/{__version__} "
    "(+https://github.com/andreagrandi/draftgoblin)"
)
ARENA_DATA_CARDS_PREFIX = "data_cards"
ARENA_DATA_LOC_PREFIX = "data_loc"
ARENA_DATA_FILE_SUFFIXES = (".mtga", ".json", ".js")
HTTP_TIMEOUT_SECONDS = 60
COLOR_ORDER = ("W", "U", "B", "R", "G")
ARENA_COLOR_ID_MAP = {1: "W", 2: "U", 3: "B", 4: "R", 5: "G"}
ARENA_RARITY_ID_MAP = {
    0: "token",
    1: "basic",
    2: "common",
    3: "uncommon",
    4: "rare",
    5: "mythic",
}
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
class CardMetadataSeed:
    """Set-scoped external metadata used to bridge grpIds to card names.
    17Lands supplies these rows before Scryfall exposes arena_id values.
    """

    grp_id: int
    name: str
    colors: tuple[str, ...]
    rarity: str


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

    def unresolved_grp_ids(self, *, grp_ids: Iterable[int]) -> tuple[int, ...]:
        """Return unique ids that still resolve to unknown markers.
        UI code uses this to warn when metadata coverage is incomplete.
        """

        seen: set[int] = set()
        unresolved: list[int] = []
        for grp_id in grp_ids:
            if grp_id in seen:
                continue

            seen.add(grp_id)
            if self.lookup(grp_id=grp_id).unknown:
                unresolved.append(grp_id)

        return tuple(unresolved)

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
    arena_data_dir: PathInput | None = None,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> CardDatabase:
    """Build and cache the grpId map from Scryfall and Arena local data.
    Passing bulk_file keeps tests and local fixtures completely offline.
    """

    if bulk_file is None:
        database = _download_or_arena_card_database(
            arena_data_dir=arena_data_dir,
            timeout_seconds=timeout_seconds,
        )
    else:
        database = build_card_database_from_bulk_file(path=bulk_file)
        if arena_data_dir is not None:
            database = augment_card_database_with_arena_data(
                database,
                arena_data_dir=arena_data_dir,
            )

    save_card_database(database, app_dir=app_dir, cache_path=cache_path)
    return database


def load_or_refresh_card_database(
    *,
    app_dir: PathInput | None = None,
    cache_path: PathInput | None = None,
    arena_data_dir: PathInput | None = None,
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
            arena_data_dir=arena_data_dir,
            timeout_seconds=timeout_seconds,
        )

    try:
        database = load_card_database(app_dir=app_dir, cache_path=cache_path)
    except CardDatabaseCacheMissingError:
        return refresh_card_database(
            app_dir=app_dir,
            cache_path=cache_path,
            arena_data_dir=arena_data_dir,
            timeout_seconds=timeout_seconds,
        )

    return augment_card_database_with_arena_data(
        database,
        arena_data_dir=arena_data_dir,
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


def build_card_database_from_arena_data_dir(*, path: PathInput) -> CardDatabase:
    """Build card metadata from MTG Arena's local data_cards/data_loc files.
    This covers day-one Arena grpIds before Scryfall publishes arena_id values.
    """

    data_dir = Path(path).expanduser()
    cards_path, loc_path = _arena_data_file_pair(path=data_dir, required=True)
    card_objects = _load_arena_json_array(path=cards_path, label="cards")
    loc_objects = _load_arena_json_array(path=loc_path, label="localization")
    localization = _arena_localization_map(items=loc_objects, source=str(loc_path))
    return build_card_database_from_arena_cards(
        cards=card_objects,
        localization=localization,
    )


def build_card_database_from_arena_cards(
    *,
    cards: Iterable[Mapping[str, Any]],
    localization: Mapping[int, str],
) -> CardDatabase:
    """Build the grpId map from Arena local card objects.
    Arena local data is authoritative for the client grpIds present in logs.
    """

    card_objects = tuple(cards)
    cards_by_grp_id: dict[int, Mapping[str, Any]] = {}
    for card_object in card_objects:
        grp_id_value = card_object.get("grpid", card_object.get("grpId"))
        if grp_id_value is None:
            continue

        grp_id = _required_int(grp_id_value, field_name="Arena card.grpid")
        cards_by_grp_id[grp_id] = card_object

    database_cards: dict[int, CardInfo] = {}
    for card_object in card_objects:
        card = _card_info_from_arena(
            card=card_object,
            localization=localization,
            cards_by_grp_id=cards_by_grp_id,
        )
        if card is None:
            continue

        database_cards[card.grp_id] = card

    return CardDatabase(cards=database_cards)


def find_default_arena_data_dir() -> Path | None:
    """Return the first default MTG Arena data dir with card metadata files.
    The function is best-effort and returns None when Arena is not installed.
    """

    for candidate in _default_arena_data_dir_candidates():
        if _arena_data_file_pair(path=candidate, required=False) is not None:
            return candidate

    return None


def augment_card_database_with_arena_data(
    database: CardDatabase,
    *,
    arena_data_dir: PathInput | None = None,
) -> CardDatabase:
    """Overlay local Arena card data when it is available.
    Local data wins because it is the source of grpIds emitted by Player.log.
    """

    arena_database = _load_arena_card_database_if_available(
        arena_data_dir=arena_data_dir,
    )
    if arena_database is None:
        return database

    return _merge_card_databases(base=database, overlay=arena_database)


def augment_card_database_with_mtgjson_set(
    database: CardDatabase,
    *,
    set_code: str,
    seeds: Iterable[CardMetadataSeed],
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
    mtgjson_cards: Iterable[Mapping[str, Any]] | None = None,
) -> CardDatabase:
    """Resolve missing grpIds by matching 17Lands names to MTGJSON set data.
    This covers new sets whose Scryfall records do not yet expose arena_id.
    """

    missing_seeds = tuple(
        seed for seed in seeds if database.lookup(grp_id=seed.grp_id).unknown
    )
    if not missing_seeds:
        return database

    card_objects = tuple(
        download_mtgjson_set_cards(
            set_code=set_code,
            timeout_seconds=timeout_seconds,
        )
        if mtgjson_cards is None
        else mtgjson_cards
    )
    cards_by_uuid = _mtgjson_cards_by_uuid(cards=card_objects)
    cards_by_name = _mtgjson_cards_by_name(cards=card_objects)
    card_indices = {id(card): index for index, card in enumerate(card_objects)}
    cards = dict(database.cards)
    inferred_offset = _mtgjson_arena_id_offset(
        seeds=missing_seeds,
        cards_by_name=cards_by_name,
        card_indices=card_indices,
    )
    if inferred_offset is not None:
        _add_mtgjson_cards_by_inferred_arena_order(
            cards=cards,
            card_objects=card_objects,
            cards_by_uuid=cards_by_uuid,
            arena_id_offset=inferred_offset,
        )

    for seed in missing_seeds:
        mtgjson_card = _mtgjson_card_for_seed(seed=seed, cards_by_name=cards_by_name)
        if mtgjson_card is None:
            cards[seed.grp_id] = _card_info_from_metadata_seed(seed=seed)
            continue

        cards[seed.grp_id] = _card_info_from_mtgjson(
            card=mtgjson_card,
            seed=seed,
            cards_by_uuid=cards_by_uuid,
        )

    return CardDatabase(cards=cards)


def download_mtgjson_set_cards(
    *,
    set_code: str,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> tuple[Mapping[str, Any], ...]:
    """Download one MTGJSON set file and return its card objects.
    MTGJSON includes current-set card names, mana values, and type lines.
    """

    url = MTGJSON_SET_URL_TEMPLATE.format(
        set_code=urllib.parse.quote(set_code.upper()),
    )
    request = _request(url=url)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise CardDatabaseError(f"Failed to query MTGJSON set metadata: {error}") from error
    except json.JSONDecodeError as error:
        raise CardDatabaseError(f"Malformed MTGJSON set metadata: {error}") from error

    if not isinstance(payload, dict):
        raise CardDatabaseError("Malformed MTGJSON set metadata: expected object.")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise CardDatabaseError("Malformed MTGJSON set metadata: missing data object.")

    cards_value = data.get("cards")
    if not isinstance(cards_value, list):
        raise CardDatabaseError("Malformed MTGJSON set metadata: missing cards list.")

    cards: list[Mapping[str, Any]] = []
    for item in cards_value:
        if not isinstance(item, dict):
            raise CardDatabaseError("Malformed MTGJSON set metadata: card is not object.")

        cards.append(item)

    return tuple(cards)


def _mtgjson_cards_by_uuid(
    *,
    cards: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    cards_by_uuid: dict[str, Mapping[str, Any]] = {}
    for card in cards:
        uuid = card.get("uuid")
        if isinstance(uuid, str) and uuid:
            cards_by_uuid[uuid] = card

    return cards_by_uuid


def _mtgjson_cards_by_name(
    *,
    cards: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    cards_by_name: dict[str, list[Mapping[str, Any]]] = {}
    for card in cards:
        for name in _mtgjson_card_names(card=card):
            cards_by_name.setdefault(_normalized_card_name(name=name), []).append(card)

    return cards_by_name


def _mtgjson_card_names(*, card: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    name = card.get("name")
    if isinstance(name, str) and name:
        names.append(name)

    face_name = card.get("faceName")
    if isinstance(face_name, str) and face_name:
        names.append(face_name)

    return tuple(dict.fromkeys(names))


def _mtgjson_card_for_seed(
    *,
    seed: CardMetadataSeed,
    cards_by_name: Mapping[str, list[Mapping[str, Any]]],
) -> Mapping[str, Any] | None:
    candidates = cards_by_name.get(_normalized_card_name(name=seed.name), [])
    if not candidates:
        return None

    return min(candidates, key=_mtgjson_card_sort_key)


def _mtgjson_arena_id_offset(
    *,
    seeds: Iterable[CardMetadataSeed],
    cards_by_name: Mapping[str, list[Mapping[str, Any]]],
    card_indices: Mapping[int, int],
) -> int | None:
    offset_counts: dict[int, int] = {}
    for seed in seeds:
        card = _mtgjson_card_for_seed(seed=seed, cards_by_name=cards_by_name)
        if card is None:
            continue

        index = card_indices.get(id(card))
        if index is None:
            continue

        offset = seed.grp_id - index
        offset_counts[offset] = offset_counts.get(offset, 0) + 1

    if not offset_counts:
        return None

    return max(offset_counts, key=lambda offset: (offset_counts[offset], -offset))


def _add_mtgjson_cards_by_inferred_arena_order(
    *,
    cards: dict[int, CardInfo],
    card_objects: tuple[Mapping[str, Any], ...],
    cards_by_uuid: Mapping[str, Mapping[str, Any]],
    arena_id_offset: int,
) -> None:
    for index, card in enumerate(card_objects):
        if not _mtgjson_card_is_arena_available(card=card):
            continue

        grp_id = arena_id_offset + index
        if grp_id in cards and not cards[grp_id].unknown:
            continue

        cards[grp_id] = _card_info_from_mtgjson(
            card=card,
            seed=_metadata_seed_from_mtgjson_card(grp_id=grp_id, card=card),
            cards_by_uuid=cards_by_uuid,
        )


def _mtgjson_card_is_arena_available(*, card: Mapping[str, Any]) -> bool:
    availability = card.get("availability")
    return isinstance(availability, list) and "arena" in availability


def _metadata_seed_from_mtgjson_card(
    *,
    grp_id: int,
    card: Mapping[str, Any],
) -> CardMetadataSeed:
    name = _required_str(card.get("name"), field_name=f"MTGJSON card {grp_id}.name")
    return CardMetadataSeed(
        grp_id=grp_id,
        name=name,
        colors=_mtgjson_colors(card=card, field_name=name),
        rarity=_required_str(
            card.get("rarity", "unknown"),
            field_name=f"MTGJSON card {name}.rarity",
        ),
    )


def _mtgjson_card_sort_key(card: Mapping[str, Any]) -> tuple[int, int, str]:
    availability = card.get("availability")
    available_on_arena = isinstance(availability, list) and "arena" in availability
    side = card.get("side")
    is_front_or_single = side in (None, "", "a")
    uuid = card.get("uuid")
    return (
        0 if available_on_arena else 1,
        0 if is_front_or_single else 1,
        uuid if isinstance(uuid, str) else "",
    )


def _card_info_from_metadata_seed(*, seed: CardMetadataSeed) -> CardInfo:
    return CardInfo(
        grp_id=seed.grp_id,
        name=seed.name,
        colors=seed.colors,
        mana_value=None,
        rarity=seed.rarity,
        types=("Unknown",),
        unknown=True,
    )


def _card_info_from_mtgjson(
    *,
    card: Mapping[str, Any],
    seed: CardMetadataSeed,
    cards_by_uuid: Mapping[str, Mapping[str, Any]],
) -> CardInfo:
    faces = _mtgjson_related_faces(card=card, cards_by_uuid=cards_by_uuid)
    type_lines = tuple(dict.fromkeys(
        _required_str(
            face.get("type"),
            field_name=f"MTGJSON card {seed.name}.type",
        )
        for face in faces
    ))
    mana_cost = _mtgjson_combined_mana_cost(faces=faces)
    return CardInfo(
        grp_id=seed.grp_id,
        name=seed.name,
        colors=seed.colors or _mtgjson_colors(card=card, field_name=seed.name),
        mana_value=_required_float(
            card.get("manaValue", card.get("mana_value")),
            field_name=f"MTGJSON card {seed.name}.manaValue",
        ),
        rarity=_required_str(
            card.get("rarity", seed.rarity),
            field_name=f"MTGJSON card {seed.name}.rarity",
        ),
        types=type_lines,
        mana_cost=mana_cost,
        produced_mana=_mtgjson_produced_mana(card=card, field_name=seed.name),
    )


def _mtgjson_related_faces(
    *,
    card: Mapping[str, Any],
    cards_by_uuid: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    faces = [card]
    other_faces_value = card.get("otherFaceIds", ())
    if isinstance(other_faces_value, list):
        for face_uuid in other_faces_value:
            if not isinstance(face_uuid, str):
                continue

            face = cards_by_uuid.get(face_uuid)
            if face is not None:
                faces.append(face)

    return tuple(faces)


def _mtgjson_combined_mana_cost(*, faces: tuple[Mapping[str, Any], ...]) -> str | None:
    costs = tuple(
        cost
        for cost in (_optional_mtgjson_mana_cost(face.get("manaCost")) for face in faces)
        if cost is not None
    )
    if not costs:
        return None

    return " // ".join(costs)


def _optional_mtgjson_mana_cost(value: Any) -> str | None:
    if value is None:
        return None

    return _required_str(value, field_name="MTGJSON card.manaCost")


def _mtgjson_colors(*, card: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    colors_value = card.get("colors")
    if colors_value is not None:
        return _color_tuple(colors_value, field_name=f"MTGJSON card {field_name}.colors")

    return _color_tuple(
        card.get("colorIdentity", ()),
        field_name=f"MTGJSON card {field_name}.colorIdentity",
    )


def _mtgjson_produced_mana(
    *,
    card: Mapping[str, Any],
    field_name: str,
) -> tuple[str, ...]:
    produced_value = card.get("producedMana")
    if produced_value is None:
        return ()

    return _produced_mana_tuple(
        produced_value,
        field_name=f"MTGJSON card {field_name}.producedMana",
    )


def _normalized_card_name(*, name: str) -> str:
    return " ".join(name.casefold().replace("’", "'").split())


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


def _card_info_from_arena(
    *,
    card: Mapping[str, Any],
    localization: Mapping[int, str],
    cards_by_grp_id: Mapping[int, Mapping[str, Any]],
) -> CardInfo | None:
    grp_id_value = card.get("grpid", card.get("grpId"))
    if grp_id_value is None:
        return None

    grp_id = _required_int(grp_id_value, field_name="Arena card.grpid")
    linked_faces = _arena_linked_face_cards(
        card=card,
        cards_by_grp_id=cards_by_grp_id,
    )
    type_line = _arena_combined_type_line(
        card=card,
        linked_faces=linked_faces,
        localization=localization,
        grp_id=grp_id,
    )
    return CardInfo(
        grp_id=grp_id,
        name=_arena_localized_text(
            localization=localization,
            text_id=card.get("titleId"),
            field_name=f"Arena card {grp_id}.titleId",
        ),
        colors=_arena_card_colors(
            card=card,
            linked_faces=linked_faces,
            grp_id=grp_id,
        ),
        mana_value=_required_float(
            card.get("cmc"),
            field_name=f"Arena card {grp_id}.cmc",
        ),
        rarity=_arena_card_rarity(card=card, grp_id=grp_id),
        types=(type_line,),
        mana_cost=_arena_card_mana_cost(card=card, linked_faces=linked_faces),
        produced_mana=_arena_card_produced_mana(
            card=card,
            primary_type_line=_arena_type_line(
                card=card,
                localization=localization,
                grp_id=grp_id,
            ),
            grp_id=grp_id,
        ),
    )


def _download_or_arena_card_database(
    *,
    arena_data_dir: PathInput | None,
    timeout_seconds: int,
) -> CardDatabase:
    try:
        database = download_scryfall_card_database(timeout_seconds=timeout_seconds)
    except CardDatabaseError:
        arena_database = _load_arena_card_database_if_available(
            arena_data_dir=arena_data_dir,
        )
        if arena_database is None:
            raise

        return arena_database

    return augment_card_database_with_arena_data(
        database,
        arena_data_dir=arena_data_dir,
    )


def _load_arena_card_database_if_available(
    *,
    arena_data_dir: PathInput | None,
) -> CardDatabase | None:
    if arena_data_dir is not None:
        return build_card_database_from_arena_data_dir(path=arena_data_dir)

    default_data_dir = find_default_arena_data_dir()
    if default_data_dir is None:
        return None

    return build_card_database_from_arena_data_dir(path=default_data_dir)


def _merge_card_databases(
    *,
    base: CardDatabase,
    overlay: CardDatabase,
) -> CardDatabase:
    cards = dict(base.cards)
    cards.update(overlay.cards)
    return CardDatabase(cards=cards)


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


def _arena_linked_face_cards(
    *,
    card: Mapping[str, Any],
    cards_by_grp_id: Mapping[int, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    linked_faces_value = card.get("linkedFaces", ())
    if linked_faces_value is None:
        return ()

    if isinstance(linked_faces_value, (str, bytes)) or not isinstance(
        linked_faces_value,
        Iterable,
    ):
        raise CardDatabaseError("Missing or invalid Arena card.linkedFaces list.")

    linked_faces: list[Mapping[str, Any]] = []
    for linked_face_value in linked_faces_value:
        linked_grp_id = _required_int(
            linked_face_value,
            field_name="Arena card.linkedFaces[]",
        )
        linked_card = cards_by_grp_id.get(linked_grp_id)
        if linked_card is not None:
            linked_faces.append(linked_card)

    return tuple(linked_faces)


def _arena_combined_type_line(
    *,
    card: Mapping[str, Any],
    linked_faces: tuple[Mapping[str, Any], ...],
    localization: Mapping[int, str],
    grp_id: int,
) -> str:
    type_lines = [
        _arena_type_line(
            card=face,
            localization=localization,
            grp_id=grp_id,
        )
        for face in (card, *linked_faces)
    ]
    unique_type_lines = tuple(dict.fromkeys(type_lines))
    return " // ".join(unique_type_lines)


def _arena_type_line(
    *,
    card: Mapping[str, Any],
    localization: Mapping[int, str],
    grp_id: int,
) -> str:
    card_type = _arena_optional_localized_text(
        localization=localization,
        text_id=card.get("cardTypeTextId"),
        field_name=f"Arena card {grp_id}.cardTypeTextId",
    )
    subtype = _arena_optional_localized_text(
        localization=localization,
        text_id=card.get("subtypeTextId"),
        field_name=f"Arena card {grp_id}.subtypeTextId",
    )
    if card_type is None:
        raise CardDatabaseError(f"Arena card {grp_id} is missing cardTypeTextId.")

    if subtype is None:
        return card_type

    return f"{card_type} — {subtype}"


def _arena_card_colors(
    *,
    card: Mapping[str, Any],
    linked_faces: tuple[Mapping[str, Any], ...],
    grp_id: int,
) -> tuple[str, ...]:
    colors: list[str] = []
    for face in (card, *linked_faces):
        colors.extend(
            _arena_color_tuple(
                face.get("colors", ()),
                field_name=f"Arena card {grp_id}.colors",
            )
        )

    return _ordered_unique_colors(colors=colors)


def _arena_card_rarity(*, card: Mapping[str, Any], grp_id: int) -> str:
    rarity_value = card.get("rarity")
    if isinstance(rarity_value, str):
        rarity = rarity_value.strip().lower().replace("mythic rare", "mythic")
        return _required_str(rarity, field_name=f"Arena card {grp_id}.rarity")

    rarity_id = _required_int(rarity_value, field_name=f"Arena card {grp_id}.rarity")
    try:
        return ARENA_RARITY_ID_MAP[rarity_id]
    except KeyError as error:
        raise CardDatabaseError(
            f"Invalid Arena rarity id in card {grp_id}.rarity: {rarity_id}."
        ) from error


def _arena_card_mana_cost(
    *,
    card: Mapping[str, Any],
    linked_faces: tuple[Mapping[str, Any], ...],
) -> str | None:
    costs = tuple(
        cost
        for cost in (
            _arena_mana_cost(face.get("castingcost", face.get("castingCost")))
            for face in (card, *linked_faces)
        )
        if cost is not None
    )
    if not costs:
        return None

    return " // ".join(costs)


def _arena_card_produced_mana(
    *,
    card: Mapping[str, Any],
    primary_type_line: str,
    grp_id: int,
) -> tuple[str, ...]:
    for field_name in (
        "produced_mana",
        "producedMana",
        "producesMana",
        "manaProduced",
    ):
        produced_value = card.get(field_name)
        if produced_value is not None:
            return _arena_mana_symbol_tuple(
                produced_value,
                field_name=f"Arena card {grp_id}.{field_name}",
            )

    if "Land" not in primary_type_line:
        return ()

    return _arena_color_tuple(
        card.get("colorIdentity", ()),
        field_name=f"Arena card {grp_id}.colorIdentity",
    )


def _arena_mana_cost(value: Any) -> str | None:
    if not isinstance(value, str) or value in {"", "o0"}:
        return None

    parts = tuple(part for part in value.split("o") if part and part != "0")
    if not parts:
        return None

    return "".join(f"{{{part}}}" for part in parts)


def _arena_localization_map(
    *,
    items: Iterable[Mapping[str, Any]],
    source: str,
) -> dict[int, str]:
    language = _select_arena_english_localization(items=items, source=source)
    keys_value = language.get("keys")
    if not isinstance(keys_value, list):
        raise CardDatabaseError(
            f"Malformed Arena localization {source}: selected language has no keys list."
        )

    localization: dict[int, str] = {}
    for item in keys_value:
        if not isinstance(item, dict):
            raise CardDatabaseError(
                f"Malformed Arena localization {source}: key entry is not object."
            )

        text_id = _required_int(item.get("id"), field_name="Arena localization id")
        text = item.get("text")
        if not isinstance(text, str):
            raise CardDatabaseError("Missing or invalid Arena localization text.")

        localization[text_id] = text

    return localization


def _select_arena_english_localization(
    *,
    items: Iterable[Mapping[str, Any]],
    source: str,
) -> Mapping[str, Any]:
    languages = tuple(items)
    if not languages:
        raise CardDatabaseError(f"Malformed Arena localization {source}: empty list.")

    for language in languages:
        langkey = str(language.get("langkey", "")).lower()
        iso_code = str(language.get("isoCode", "")).lower()
        if langkey in {"en", "english"} or iso_code in {"en", "en-us"}:
            return language

    return languages[0]


def _arena_localized_text(
    *,
    localization: Mapping[int, str],
    text_id: Any,
    field_name: str,
) -> str:
    text = _arena_optional_localized_text(
        localization=localization,
        text_id=text_id,
        field_name=field_name,
    )
    if text is None:
        raise CardDatabaseError(f"Missing localization for {field_name}.")

    return text


def _arena_optional_localized_text(
    *,
    localization: Mapping[int, str],
    text_id: Any,
    field_name: str,
) -> str | None:
    if text_id is None:
        return None

    resolved_text_id = _required_int(text_id, field_name=field_name)
    if resolved_text_id == 0:
        return None

    text = localization.get(resolved_text_id)
    if text is None:
        raise CardDatabaseError(f"Missing localization for {field_name}.")

    if text == "":
        return None

    return text


def _arena_data_file_pair(
    *,
    path: Path,
    required: bool,
) -> tuple[Path, Path] | None:
    cards_path = _latest_arena_data_file(path=path, prefix=ARENA_DATA_CARDS_PREFIX)
    loc_path = _latest_arena_data_file(path=path, prefix=ARENA_DATA_LOC_PREFIX)
    if cards_path is not None and loc_path is not None:
        return cards_path, loc_path

    if required:
        raise CardDatabaseError(
            f"Arena local data at {path} is missing data_cards*.mtga or data_loc*.mtga."
        )

    return None


def _latest_arena_data_file(*, path: Path, prefix: str) -> Path | None:
    if not path.is_dir():
        return None

    candidates = tuple(
        candidate
        for candidate in path.iterdir()
        if candidate.is_file()
        and candidate.name.startswith(prefix)
        and candidate.suffix.lower() in ARENA_DATA_FILE_SUFFIXES
    )
    if not candidates:
        return None

    return max(candidates, key=_arena_data_file_sort_key)


def _arena_data_file_sort_key(path: Path) -> tuple[float, str]:
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        modified_at = 0.0

    return modified_at, path.name


def _load_arena_json_array(*, path: Path, label: str) -> tuple[Mapping[str, Any], ...]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise CardDatabaseError(f"Could not read Arena {label} file {path}: {error}.") from error

    payload = _strip_javascript_assignment(text=text)
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CardDatabaseError(
            f"Malformed Arena {label} JSON at {path}: {error.msg}."
        ) from error

    if not isinstance(value, list):
        raise CardDatabaseError(f"Malformed Arena {label} JSON at {path}: expected list.")

    objects: list[Mapping[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise CardDatabaseError(
                f"Malformed Arena {label} JSON at {path}:{index}: expected object."
            )

        objects.append(item)

    return tuple(objects)


def _strip_javascript_assignment(*, text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("var ") and "=" in stripped:
        stripped = stripped.split("=", 1)[1].strip()

    return stripped.rstrip(";").strip()


def _default_arena_data_dir_candidates() -> tuple[Path, ...]:
    current_system = platform.system()
    home = Path.home()
    if current_system == "Darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "com.wizards.mtga"
            / "Downloads"
            / "Data",
        )

    if current_system == "Windows":
        candidates: list[Path] = []
        registry_path = _windows_registry_arena_data_dir()
        if registry_path is not None:
            candidates.append(registry_path)

        for root_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(root_name)
            if root is None:
                continue

            candidates.append(
                Path(root)
                / "Wizards of the Coast"
                / "MTGA"
                / "MTGA_Data"
                / "Downloads"
                / "Data"
            )

        return tuple(candidates)

    return ()


def _windows_registry_arena_data_dir() -> Path | None:
    if platform.system() != "Windows":
        return None

    try:
        import winreg
    except ImportError:
        return None

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Wizards of the Coast\MTGArena",
        ) as registry_key:
            install_path, _ = winreg.QueryValueEx(registry_key, "Path")
    except OSError:
        return None

    if not isinstance(install_path, str):
        return None

    return Path(install_path) / "MTGA_Data" / "Downloads" / "Data"


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


def _arena_color_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise CardDatabaseError(f"Missing or invalid {field_name}; expected id list.")

    colors: list[str] = []
    for item in value:
        color_id = _required_int(item, field_name=field_name)
        try:
            colors.append(ARENA_COLOR_ID_MAP[color_id])
        except KeyError as error:
            raise CardDatabaseError(
                f"Invalid Arena color id in {field_name}: {color_id}."
            ) from error

    return _ordered_unique_colors(colors=colors)


def _arena_mana_symbol_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise CardDatabaseError(f"Missing or invalid {field_name}; expected mana list.")

    colors: list[str] = []
    for item in value:
        if isinstance(item, str):
            if item == "C":
                continue
            if item not in COLOR_ORDER:
                raise CardDatabaseError(
                    f"Invalid Arena mana value in {field_name}: {item}."
                )

            colors.append(item)
            continue

        color_id = _required_int(item, field_name=field_name)
        try:
            colors.append(ARENA_COLOR_ID_MAP[color_id])
        except KeyError as error:
            raise CardDatabaseError(
                f"Invalid Arena mana id in {field_name}: {color_id}."
            ) from error

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

