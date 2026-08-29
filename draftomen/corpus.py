"""Reproducible, development-only card corpus acquisition and normalization.

This module deliberately has no call sites in the live card database.  It owns the
larger, pinned inputs used while designing semantic classifiers and exposes only
JSONL loading to future offline consumers.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias

from draftomen.carddb import (
    ARENA_COLOR_ID_MAP,
    ARENA_RARITY_ID_MAP,
    CardDatabaseError,
    SCRYFALL_BULK_DATA_URL,
    build_card_database_from_arena_data_dir,
)

PathInput: TypeAlias = str | os.PathLike[str]
ARENA_CARD_FILE_PREFIXES = ("data_cards", "Raw_CardDatabase")
ARENA_LOCALIZATION_FILE_PREFIXES = ("data_loc", "Raw_ClientLocalization")
CORPUS_SCHEMA_VERSION = 2
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_CACHE_DIR = Path(".draftomen") / "corpus-cache"
DEFAULT_ARTIFACT_DIR = Path(".draftomen") / "corpus-artifacts"
LOCK_FILENAME = "sources.lock.json"
MANIFEST_FILENAME = "sources.manifest.json"
NORMALIZED_FILENAME = "normalized.jsonl"
REPORT_FILENAME = "coverage.json"

SCRYFALL_ATTRIBUTION = "Scryfall (https://scryfall.com)"
SCRYFALL_LICENSE = (
    "Scryfall API and data use is subject to the "
    "Scryfall API Terms (https://scryfall.com/docs/api) and "
    "Wizards Fan Content Policy (https://company.wizards.com/en/legal/fancontentpolicy); "
    "no separate Scryfall data license is asserted."
)
MTGJSON_ATTRIBUTION = "MTGJSON (https://mtgjson.com)"
MTGJSON_LICENSE = (
    "MTGJSON is published under the MIT License "
    "(https://github.com/mtgjson/mtgjson/blob/master/LICENSE.txt); "
    "underlying Magic: The Gathering content remains subject to Wizards terms."
)
ARENA_ATTRIBUTION = "Magic: The Gathering Arena client data"
ARENA_LICENSE = (
    "Redistributed only as local user-provided input; "
    "underlying Magic: The Gathering content remains subject to Wizards terms."
)

SUPPORTED_LAYOUTS = frozenset(
    {
        "normal",
        "split",
        "flip",
        "transform",
        "modal_dfc",
        "meld",
        "leveler",
        "saga",
        "class",
        "prototype",
        "adventure",
        "reversible_card",
        "augment",
        "host",
        "planar",
        "scheme",
        "vanguard",
        "phenomenon",
        "token",
        "double_faced_token",
        "emblem",
        "art_series",
        "battle",
        "case",
    }
)
# These patterns are descriptive coverage signals, not classifier labels. They
# make gaps visible without making correctness depend on Scryfall tag data.
WORDING_PATTERNS = (
    ("enters_battlefield", re.compile(r"enters the battlefield", re.I)),
    ("dies", re.compile(r"\bdies\b", re.I)),
    ("exile", re.compile(r"\bexile\b", re.I)),
    ("sacrifice", re.compile(r"\bsacrifice\w*\b", re.I)),
    ("transform", re.compile(r"\btransform\w*\b", re.I)),
    ("adventure", re.compile(r"\badventure\b", re.I)),
    ("modal_choice", re.compile(r"choose one|choose two|modal", re.I)),
    ("counter", re.compile(r"\bcounter\b", re.I)),
    ("draft_or_conjure", re.compile(r"\b(?:draft|conjure)\w*\b", re.I)),
)
SEMANTIC_FIELDS = (
    "oracle_text",
    "type_line",
    "layout",
    "colors",
    "mana_value",
    "rarity",
)


class CorpusError(RuntimeError):
    """Base error for corpus configuration, acquisition, and parsing failures."""


class CorpusOfflineError(CorpusError):
    """Raised when an offline build needs a missing or invalid cached source."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """One pinned input, either a URL or a local Arena/source file."""

    name: str
    kind: str
    url: str | None = None
    path: str | None = None
    sha256: str | None = None
    version: str | None = None
    etag: str | None = None
    attribution: str = ""
    license: str = ""
    required: bool = True
    set_code: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.name
            or Path(self.name).name != self.name
            or "/" in self.name
            or "\\" in self.name
            or self.name in {".", ".."}
        ):
            raise CorpusError(f"Invalid source name {self.name!r}.")
        if self.kind not in {"scryfall", "mtgjson", "arena"}:
            raise CorpusError(f"Unsupported corpus source kind {self.kind!r}.")
        if (self.url is None) == (self.path is None):
            raise CorpusError(f"Source {self.name!r} must have exactly one of url or path.")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256):
            raise CorpusError(f"Source {self.name!r} has an invalid SHA-256 pin.")

    @property
    def cache_filename(self) -> str:
        # Keep the filename stable when a Scryfall discovery URL resolves to
        # a versioned ``default-cards-*.jsonl.gz`` URL on a later run.
        source_path = Path(urllib.parse.urlparse(self.url or self.path or "").path)
        if self.kind == "scryfall":
            if self.url is not None:
                # A discovery endpoint may resolve to either the current
                # ``jsonl_download_uri`` or the legacy ``download_uri``.
                # Keep one cache name across both URLs and locked reruns.
                return f"{self.name}.jsonl.gz"
            suffixes = "".join(source_path.suffixes)
            if suffixes not in {".jsonl", ".jsonl.gz", ".json.gz"}:
                suffixes = ".jsonl.gz"
            return f"{self.name}{suffixes}"
        if self.kind == "mtgjson":
            suffix = source_path.suffix
            if suffix != ".json":
                suffix = ".json"
            return f"{self.name}{suffix}"
        suffix = source_path.suffix or ".bin"
        return f"{self.name}{suffix}"

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "name": self.name,
            "kind": self.kind,
            "sha256": self.sha256,
            "version": self.version,
            "etag": self.etag,
            "attribution": self.attribution,
            "license": self.license,
            "required": self.required,
        }
        if self.url is not None:
            value["url"] = self.url
        if self.path is not None:
            value["path"] = self.path
        if self.set_code is not None:
            value["set_code"] = self.set_code
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> SourceSpec:
        def optional_string(key: str) -> str | None:
            item = value.get(key)
            if item is None:
                return None
            if not isinstance(item, str):
                raise CorpusError(f"Source field {key!r} must be a string.")
            return item

        name = value.get("name")
        kind = value.get("kind")
        if not isinstance(name, str) or not isinstance(kind, str):
            raise CorpusError("Each corpus source needs string name and kind fields.")
        return cls(
            name=name,
            kind=kind,
            url=optional_string("url"),
            path=optional_string("path"),
            sha256=optional_string("sha256"),
            version=optional_string("version"),
            etag=optional_string("etag"),
            attribution=str(value.get("attribution", "")),
            license=str(value.get("license", "")),
            required=bool(value.get("required", True)),
            set_code=optional_string("set_code"),
        )


@dataclass(frozen=True, slots=True)
class SelectionSpec:
    """Stable corpus selection policy.

    ``broad`` includes the supplied current/default corpus, HOB, cards released
    from 2018 onward, and every record carrying a supported multi-face layout.
    Explicit set codes are an additional allow-list and are case-insensitive.
    """

    mode: str = "broad"
    sets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"broad", "explicit"}:
            raise CorpusError(f"Unknown corpus selection mode {self.mode!r}.")
        normalized = tuple(dict.fromkeys(item.strip().lower() for item in self.sets if item.strip()))
        object.__setattr__(self, "sets", normalized)
        if self.mode == "explicit" and not normalized:
            raise CorpusError("Explicit corpus selection requires at least one set code.")

    def to_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "sets": list(self.sets),
            "policy": (
                "current/default Scryfall cards; HOB; Arena-era releases from 2018 onward; "
                "and all supported multi-face layouts"
                if self.mode == "broad"
                else "only explicitly requested set codes"
            ),
        }


@dataclass(frozen=True, slots=True)
class AcquiredSource:
    spec: SourceSpec
    path: Path
    sha256: str
    retrieved_at: str
    resolved_url: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    sources: tuple[AcquiredSource, ...]
    lock_path: Path
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class SelectionResult:
    cards: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        return iter(self.cards)

    def __len__(self) -> int:
        return len(self.cards)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_bytes(value: Any, *, indent: int | None = None) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary_name: str | None = None
    try:
        with source.open("rb") as source_file, tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        return digest.hexdigest()
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_url(
    url: str, destination: Path, *, timeout_seconds: int = 60
) -> tuple[str, str | None, str | None]:
    """Stream one remote source into an atomically replaced cache file."""

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "draftomen-corpus/1", "Accept": "application/json, application/gzip"},
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary_name: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response, tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
            etag = _response_header(response, "ETag")
            version = _response_header(response, "Last-Modified")
        os.replace(temporary_name, destination)
        temporary_name = None
        return digest.hexdigest(), etag, version
    except (OSError, urllib.error.URLError) as error:
        raise CorpusError(f"Could not download corpus source {url}: {error}.") from error
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _safe_cache_path(cache_dir: Path, name: str) -> Path:
    if not name or Path(name).name != name or name in {".", ".."} or "/" in name or "\\" in name:
        raise CorpusError(f"Unsafe source cache path {name!r}.")
    return cache_dir / "sources" / name


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusError(f"Could not parse JSON source {path}: {error}.") from error


def _response_header(response: Any, key: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get(key)
    return value if isinstance(value, str) else None


_MAX_DISCOVERY_BYTES = 4 * 1024 * 1024


def _fetch_url(url: str, *, timeout_seconds: int = 60) -> tuple[bytes, str | None, str | None]:
    """Read a small discovery response with an explicit memory bound."""

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "draftomen-corpus/1", "Accept": "application/json, application/gzip"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = bytearray()
            while True:
                chunk = response.read(min(64 * 1024, _MAX_DISCOVERY_BYTES - len(payload) + 1))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > _MAX_DISCOVERY_BYTES:
                    raise CorpusError(f"Discovery response from {url} exceeds {_MAX_DISCOVERY_BYTES} bytes.")
            return bytes(payload), _response_header(response, "ETag"), _response_header(response, "Last-Modified")
    except CorpusError:
        raise
    except (OSError, urllib.error.URLError) as error:
        raise CorpusError(f"Could not download corpus source {url}: {error}.") from error


def _resolve_scryfall_source(spec: SourceSpec, *, timeout_seconds: int) -> tuple[str, str | None, str | None]:
    if spec.url is None or not spec.url.rstrip("/").endswith("bulk-data"):
        return spec.url or "", spec.etag, spec.version
    payload, etag, version = _fetch_url(spec.url, timeout_seconds=timeout_seconds)
    try:
        value = json.loads(payload)
        data = value["data"]
        item = next(item for item in data if item.get("type") == "default_cards")
        resolved_url = item.get("jsonl_download_uri") or item.get("download_uri")
    except (KeyError, TypeError, StopIteration, json.JSONDecodeError) as error:
        raise CorpusError("Scryfall bulk-data response has no default_cards download URI.") from error
    if not isinstance(resolved_url, str) or not resolved_url.startswith(("https://", "http://")):
        raise CorpusError("Scryfall returned an invalid default_cards download URI.")
    return resolved_url, etag, version or str(item.get("updated_at", "")) or None


def _locked_specs(lock_path: Path) -> tuple[SourceSpec, ...]:
    value = _read_json(lock_path)
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CorpusError(f"Invalid corpus source lock {lock_path}.")
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise CorpusError(f"Corpus source lock {lock_path} has no sources list.")
    if not all(isinstance(item, dict) for item in sources):
        raise CorpusError(f"Corpus source lock {lock_path} contains an invalid source.")
    return tuple(SourceSpec.from_json(item) for item in sources)


def _manifest_retrieval_times(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        value = _read_json(path)
    except CorpusError:
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
        return {}
    return {
        item["name"]: item["retrieved_at"]
        for item in value["sources"]
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("retrieved_at"), str)
    }


def acquire_sources(
    *,
    source_specs: Iterable[SourceSpec],
    cache_dir: PathInput = DEFAULT_CACHE_DIR,
    lock_path: PathInput | None = None,
    offline: bool = False,
    timeout_seconds: int = 60,
) -> AcquisitionResult:
    """Acquire sources atomically, pin checksums, and keep retrieval metadata separate.

    Existing locks are authoritative. Every reused file is hashed before it is
    returned; ``offline`` never invokes urllib and fails on any cache miss. A
    cache file from an unpinned first run is never treated as source data: it is
    replaced by a fresh copy/download before the first lock is written.
    """

    cache = Path(cache_dir).expanduser()
    resolved_lock_path = Path(lock_path) if lock_path is not None else cache / LOCK_FILENAME
    resolved_manifest_path = cache / MANIFEST_FILENAME
    lock_exists = resolved_lock_path.exists()
    specs = _locked_specs(resolved_lock_path) if lock_exists else tuple(source_specs)
    if not specs:
        raise CorpusError("No corpus sources were configured.")
    previous_retrieval = _manifest_retrieval_times(resolved_manifest_path) if lock_exists else {}
    acquired: list[AcquiredSource] = []
    lock_specs: list[SourceSpec] = []
    manifest_sources: list[dict[str, object]] = []
    for spec in specs:
        cache_path = _safe_cache_path(cache, spec.cache_filename)
        expected = spec.sha256
        resolved_url = spec.url
        etag = spec.etag
        version = spec.version
        actual: str | None = None
        acquired_now = False
        cache_exists = cache_path.exists()
        # Before the first lock, an unpinned cache file is not evidence of a
        # source. It must be replaced from the configured path or URL.
        if cache_exists and not lock_exists and expected is None:
            if offline:
                if spec.required:
                    raise CorpusOfflineError(
                        f"Offline corpus source cache miss or unpinned cache for {spec.name}: {cache_path}."
                    )
                continue
            cache_exists = False
        if cache_exists:
            actual = _sha256(cache_path)
            if expected is not None and actual.lower() != expected.lower():
                raise CorpusError(
                    f"Checksum mismatch for cached source {spec.name}: expected {expected}, got {actual}."
                )
        elif offline:
            if spec.required:
                raise CorpusOfflineError(
                    f"Offline corpus source cache miss for {spec.name}: {cache_path}."
                )
            continue
        else:
            if spec.path is not None:
                source_path = Path(spec.path).expanduser()
                if not source_path.is_file():
                    if spec.required:
                        raise CorpusError(f"Required corpus source does not exist: {source_path}.")
                    continue
                actual = _atomic_copy(source_path, cache_path)
            else:
                resolved_url, discovery_etag, discovery_version = _resolve_scryfall_source(
                    spec, timeout_seconds=timeout_seconds
                )
                actual, download_etag, download_version = _download_url(
                    resolved_url, cache_path, timeout_seconds=timeout_seconds
                )
                etag = download_etag or discovery_etag or etag
                version = download_version or discovery_version or version
            acquired_now = True
            if expected is not None and actual.lower() != expected.lower():
                cache_path.unlink(missing_ok=True)
                raise CorpusError(
                    f"Checksum mismatch for source {spec.name}: expected {expected}, got {actual}."
                )
        if actual is None:
            raise CorpusError(f"Could not acquire corpus source {spec.name}.")
        locked_spec = SourceSpec(
            name=spec.name,
            kind=spec.kind,
            url=resolved_url if spec.url is not None else None,
            path=None if spec.url is not None else spec.path,
            sha256=actual,
            version=version,
            etag=etag,
            attribution=spec.attribution or _default_attribution(spec.kind),
            license=spec.license or _default_license(spec.kind),
            required=spec.required,
            set_code=spec.set_code,
        )
        lock_specs.append(locked_spec)
        retrieved_at = (
            previous_retrieval.get(spec.name)
            if lock_exists and not acquired_now
            else None
        ) or _utc_now()
        acquired.append(
            AcquiredSource(
                spec=locked_spec,
                path=cache_path,
                sha256=actual,
                retrieved_at=retrieved_at,
                resolved_url=resolved_url,
            )
        )
        manifest_sources.append(
            {
                **locked_spec.to_json(),
                "path": str(cache_path.relative_to(cache)),
                "retrieved_at": retrieved_at,
            }
        )
    if not acquired:
        raise CorpusOfflineError("No corpus sources are available for the requested build.")
    # The manifest is the complete retrieval record. Publish it first so a
    # failed manifest write can never be followed by a new authoritative lock.
    _atomic_write(
        resolved_manifest_path,
        _json_bytes(
            {"schema_version": MANIFEST_SCHEMA_VERSION, "sources": manifest_sources}, indent=2
        ),
    )
    _atomic_write(
        resolved_lock_path,
        _json_bytes(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "sources": [spec.to_json() for spec in lock_specs],
            },
            indent=2,
        ),
    )
    return AcquisitionResult(
        sources=tuple(acquired), lock_path=resolved_lock_path, manifest_path=resolved_manifest_path
    )


def _default_attribution(kind: str) -> str:
    return {"scryfall": SCRYFALL_ATTRIBUTION, "mtgjson": MTGJSON_ATTRIBUTION, "arena": ARENA_ATTRIBUTION}[kind]


def _default_license(kind: str) -> str:
    return {"scryfall": SCRYFALL_LICENSE, "mtgjson": MTGJSON_LICENSE, "arena": ARENA_LICENSE}[kind]


def load_source_config(path: PathInput) -> tuple[tuple[SourceSpec, ...], SelectionSpec]:
    """Load tracked source/selection configuration without touching the network."""

    config_path = Path(path)
    value = _read_json(config_path)
    if not isinstance(value, dict):
        raise CorpusError(f"Corpus source config {config_path} must be an object.")
    if value.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise CorpusError(f"Invalid corpus source config {config_path}.")
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list):
        raise CorpusError(f"Corpus source config {config_path} has no sources list.")
    specs = []
    for item in raw_sources:
        if not isinstance(item, dict):
            raise CorpusError(f"Corpus source config {config_path} contains a non-object source.")
        item = dict(item)
        local_path = item.get("path")
        if isinstance(local_path, str) and not Path(local_path).is_absolute():
            item["path"] = str((config_path.parent / local_path).resolve())
        specs.append(SourceSpec.from_json(item))
    raw_selection = value.get("selection", {"mode": "broad"})
    if not isinstance(raw_selection, dict):
        raise CorpusError("Corpus source config selection must be an object.")
    sets = raw_selection.get("sets", ())
    if not isinstance(sets, (list, tuple)) or not all(isinstance(item, str) for item in sets):
        raise CorpusError("Corpus selection sets must be a list of strings.")
    return tuple(specs), SelectionSpec(mode=str(raw_selection.get("mode", "broad")), sets=tuple(sets))


def build_default_source_specs(
    *,
    arena_data_dir: PathInput | None = None,
    set_codes: Iterable[str] = (),
    scryfall_file: PathInput | None = None,
    mtgjson_files: Iterable[PathInput] = (),
) -> tuple[SourceSpec, ...]:
    """Build source specs using the same Arena directory shape as ``carddb``."""

    specs: list[SourceSpec] = []
    if scryfall_file is None:
        specs.append(
            SourceSpec(
                name="scryfall-default-cards",
                kind="scryfall",
                url=SCRYFALL_BULK_DATA_URL,
                attribution=SCRYFALL_ATTRIBUTION,
                license=SCRYFALL_LICENSE,
            )
        )
    else:
        specs.append(
            SourceSpec(
                name="scryfall-default-cards",
                kind="scryfall",
                path=str(Path(scryfall_file).expanduser()),
                attribution=SCRYFALL_ATTRIBUTION,
                license=SCRYFALL_LICENSE,
            )
        )
    if arena_data_dir is not None:
        directory = Path(arena_data_dir).expanduser()
        cards = _find_arena_file(directory, prefixes=ARENA_CARD_FILE_PREFIXES)
        localization = _find_arena_file(
            directory, prefixes=ARENA_LOCALIZATION_FILE_PREFIXES
        )
        if cards is None or localization is None:
            raise CorpusError(
                f"Arena local data at {directory} is missing card database or "
                "localization files."
            )
        if not _is_sqlite_file(cards):
            try:
                # Validate legacy JSON inputs with the production parser first.
                build_card_database_from_arena_data_dir(path=directory)
            except CardDatabaseError as error:
                raise CorpusError(
                    f"Invalid Arena mapping input at {directory}: {error}"
                ) from error
        specs.extend(
            (
                SourceSpec(
                    name="arena-cards",
                    kind="arena",
                    path=str(cards),
                    attribution=ARENA_ATTRIBUTION,
                    license=ARENA_LICENSE,
                ),
                SourceSpec(
                    name="arena-localization",
                    kind="arena",
                    path=str(localization),
                    attribution=ARENA_ATTRIBUTION,
                    license=ARENA_LICENSE,
                ),
            )
        )
    for index, path in enumerate(mtgjson_files):
        set_code = Path(path).stem.lower()
        specs.append(
            SourceSpec(
                name=f"mtgjson-{set_code}-{index}",
                kind="mtgjson",
                path=str(Path(path).expanduser()),
                set_code=set_code,
                attribution=MTGJSON_ATTRIBUTION,
                license=MTGJSON_LICENSE,
            )
        )
    for set_code in dict.fromkeys(code.strip().lower() for code in set_codes if code.strip()):
        specs.append(
            SourceSpec(
                name=f"mtgjson-{set_code}",
                kind="mtgjson",
                url=f"https://mtgjson.com/api/v5/{urllib.parse.quote(set_code.upper())}.json",
                set_code=set_code,
                attribution=MTGJSON_ATTRIBUTION,
                license=MTGJSON_LICENSE,
            )
        )
    return tuple(specs)


def _find_arena_file(directory: Path, *, prefixes: tuple[str, ...]) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = tuple(
        item
        for item in directory.iterdir()
        if item.is_file()
        and item.name.startswith(prefixes)
        and item.suffix.lower() in {".mtga", ".json", ".js"}
    )
    return (
        max(candidates, key=lambda item: (item.stat().st_mtime, item.name))
        if candidates
        else None
    )


def _open_bulk(path: Path) -> io.TextIOBase:
    if path.suffix.lower() == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8-sig")
    return path.open(mode="r", encoding="utf-8-sig")


_MAX_JSON_DOCUMENT_BYTES = 512 * 1024 * 1024


def _jsonl_objects(path: Path) -> Iterator[Mapping[str, Any]]:
    try:
        with _open_bulk(path) as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as error:
                    raise CorpusError(
                        f"Malformed JSONL source {path}:{line_number}: {error}."
                    ) from error
                if not isinstance(item, dict):
                    raise CorpusError(f"JSONL source {path}:{line_number} contains a non-object.")
                yield item
    except (OSError, EOFError, UnicodeError) as error:
        raise CorpusError(f"Could not read card source {path}: {error}.") from error


def _json_objects(path: Path) -> tuple[Mapping[str, Any], ...]:
    if ".jsonl" in path.name.lower():
        return tuple(_jsonl_objects(path))
    try:
        with _open_bulk(path) as stream:
            text = stream.read(_MAX_JSON_DOCUMENT_BYTES + 1)
    except (OSError, EOFError, UnicodeError) as error:
        raise CorpusError(f"Could not read card source {path}: {error}.") from error
    if len(text) > _MAX_JSON_DOCUMENT_BYTES:
        raise CorpusError(f"JSON source {path} exceeds {_MAX_JSON_DOCUMENT_BYTES} bytes.")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return tuple(_jsonl_objects(path))
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise CorpusError(f"JSON source {path} contains a non-object card.")
        return tuple(value)
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, dict) and isinstance(data.get("cards"), list):
            cards = data["cards"]
            if all(isinstance(item, dict) for item in cards):
                return tuple(cards)
        if isinstance(value.get("cards"), list) and all(isinstance(item, dict) for item in value["cards"]):
            return tuple(value["cards"])
    raise CorpusError(f"Unsupported card source JSON shape at {path}.")


def _is_sqlite_file(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(16) == b"SQLite format 3\x00"
    except OSError as error:
        raise CorpusError(f"Could not inspect Arena source {path}: {error}.") from error


def _arena_integer_list(value: Any) -> list[int]:
    if not isinstance(value, str):
        return []
    return [int(item) for item in re.findall(r"-?\d+", value)]


def _arena_mana_value(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    mana_value = 0
    for symbol in (item for item in value.split("o") if item and item != "0"):
        if symbol.isdigit():
            mana_value += int(symbol)
        elif symbol[0].isdigit() and "/" in symbol:
            mana_value += int(symbol.split("/", 1)[0])
        elif symbol.upper() not in {"X", "Y", "Z"}:
            mana_value += 1
    return float(mana_value)


def _arena_sqlite_records(path: Path) -> tuple[Mapping[str, Any], ...]:
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as database:
            database.row_factory = sqlite3.Row
            tables = {
                row["name"]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "Cards" not in tables:
                return ()
            if "Localizations_enUS" not in tables:
                raise CorpusError(
                    f"Arena card database {path} has no Localizations_enUS table."
                )
            localizations = [
                {"id": row["LocId"], "text": row["Loc"]}
                for row in database.execute(
                    "SELECT LocId, Loc FROM Localizations_enUS "
                    "WHERE Formatted = 1 AND Loc IS NOT NULL"
                )
            ]
            cards = [
                {
                    "grpid": row["GrpId"],
                    "titleId": row["TitleId"],
                    "cmc": _arena_mana_value(row["OldSchoolManaText"]),
                    "rarity": row["Rarity"],
                    "cardTypeTextId": row["TypeTextId"],
                    "subtypeTextId": row["SubtypeTextId"],
                    "colors": _arena_integer_list(row["Colors"]),
                    "colorIdentity": _arena_integer_list(row["ColorIdentity"]),
                    "castingcost": row["OldSchoolManaText"],
                    "linkedFaces": _arena_integer_list(row["LinkedFaceGrpIds"]),
                }
                for row in database.execute(
                    "SELECT GrpId, TitleId, TypeTextId, SubtypeTextId, Rarity, "
                    "OldSchoolManaText, Colors, ColorIdentity, LinkedFaceGrpIds "
                    "FROM Cards"
                )
            ]
    except sqlite3.Error as error:
        raise CorpusError(f"Could not read Arena SQLite source {path}: {error}.") from error
    return (
        *cards,
        {"langkey": "EN", "isoCode": "en-US", "keys": localizations},
    )


def _arena_json(path: Path) -> tuple[Mapping[str, Any], ...]:
    if _is_sqlite_file(path):
        return _arena_sqlite_records(path)
    text = path.read_text(encoding="utf-8-sig").strip()
    if text.startswith("var ") and "=" in text:
        text = text.split("=", 1)[1].strip()
    text = text.rstrip(";").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise CorpusError(f"Malformed Arena JSON at {path}: {error}.") from error
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CorpusError(f"Arena source {path} must contain a JSON object array.")
    return tuple(value)


def _source_cards(
    acquired: Sequence[AcquiredSource],
) -> tuple[
    Iterator[Mapping[str, Any]],
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
]:
    mtgjson: list[Mapping[str, Any]] = []
    arena: list[Mapping[str, Any]] = []
    scryfall_sources = tuple(source for source in acquired if source.spec.kind == "scryfall")
    for source in acquired:
        if source.spec.kind == "arena":
            arena.extend(_arena_json(source.path))
        elif source.spec.kind == "mtgjson":
            mtgjson.extend(_json_objects(source.path))

    def iter_scryfall() -> Iterator[Mapping[str, Any]]:
        for source in scryfall_sources:
            if ".jsonl" in source.path.name.lower():
                yield from _jsonl_objects(source.path)
            else:
                yield from _json_objects(source.path)

    return iter_scryfall(), tuple(arena), tuple(mtgjson)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _ordered_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            values.append(item)
    return values


def _shared_face_text(
    faces: Sequence[Mapping[str, Any]], field_name: str
) -> str | None:
    values = tuple(_text(face.get(field_name)) for face in faces)
    if not values or any(value is None for value in values):
        return None
    if len(set(values)) != 1:
        return None
    return values[0]


def _colors(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, int) and item in ARENA_COLOR_ID_MAP:
                item = ARENA_COLOR_ID_MAP[item]
            if isinstance(item, str) and item.upper() in {"W", "U", "B", "R", "G"}:
                values.append(item.upper())
    return [item for item in ("W", "U", "B", "R", "G") if item in values]


def _color_field(value: Any) -> list[str] | None:
    return None if value is None else _colors(value)


def _mana_cost(value: Any) -> str | None:
    text = _text(value)
    if text is None or text.lower() == "o0":
        return None
    if "o" in text and "{" not in text:
        parts = tuple(part for part in text.split("o") if part and part != "0")
        return "".join(f"{{{part}}}" for part in parts) or None
    return text


def _type_line_parts(type_line: str | None) -> tuple[str | None, list[str]]:
    if type_line is None:
        return None, []
    if "—" in type_line:
        separator = "—"
    elif "-" in type_line:
        separator = "-"
    else:
        return type_line, []
    _, subtype = type_line.split(separator, 1)
    return type_line, [item for item in re.split(r"[ —-]+", subtype.strip()) if item]


def _normal_identity(value: Any) -> str:
    if value is None:
        return ""
    return _normal_name(value if isinstance(value, str) else str(value))


def _normal_name(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _card_sort_key(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
    return (
        _normal_name(_text(row.get("set"))),
        _normal_name(_text(row.get("collector_number"))),
        int(row.get("grp_id") or 0),
        _normal_name(_text(row.get("name"))),
    )


def _arena_localization(items: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    languages = tuple(item for item in items if isinstance(item.get("keys"), list))
    selected = next(
        (
            language
            for language in languages
            if str(language.get("langkey", language.get("isoCode", ""))).lower()
            in {"en", "english", "en-us"}
        ),
        languages[0] if languages else None,
    )
    if selected is None:
        return {}
    localization_map: dict[int, str] = {}
    for item in selected["keys"]:
        if not isinstance(item, dict):
            continue
        parsed_id = _int(item.get("id"))
        text = _text(item.get("text"))
        if parsed_id is not None and text is not None:
            localization_map[parsed_id] = text
    return localization_map


def _arena_name(card: Mapping[str, Any], localization: Mapping[int, str]) -> str | None:
    for field in ("name", "title", "cardName"):
        result = _text(card.get(field))
        if result:
            return result
    for field in ("titleId", "nameId", "cardNameTextId"):
        text_id = _int(card.get(field))
        if text_id is not None and text_id in localization:
            return localization[text_id]
    return None


def _arena_type_line(card: Mapping[str, Any], localization: Mapping[int, str]) -> str | None:
    for field in ("type_line", "typeLine", "type"):
        result = _text(card.get(field))
        if result:
            return result
    card_type = localization.get(_int(card.get("cardTypeTextId")) or -1)
    subtype = localization.get(_int(card.get("subtypeTextId")) or -1)
    if card_type and subtype:
        return f"{card_type} — {subtype}"
    return card_type


def _arena_produced_mana(
    card: Mapping[str, Any], *, type_line: str | None
) -> list[str] | None:
    for field_name in (
        "produced_mana",
        "producedMana",
        "producesMana",
        "manaProduced",
    ):
        value = card.get(field_name)
        if value is not None:
            return _colors(value)
    if type_line is not None and "Land" in type_line:
        return _colors(card.get("colorIdentity", []))
    return None


def _arena_records(records: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    localization_items = tuple(item for item in records if "keys" in item)
    localization = _arena_localization(localization_items)
    cards = tuple(item for item in records if "keys" not in item)
    by_id = {_int(item.get("grpid", item.get("grpId", item.get("grp_id")))): item for item in cards}
    normalized: list[Mapping[str, Any]] = []
    for card in cards:
        grp_id = _int(card.get("grpid", card.get("grpId", card.get("grp_id"))))
        if grp_id is None:
            continue
        faces: list[Mapping[str, Any]] = []
        links = card.get("linkedFaces", ())
        if isinstance(links, list):
            for link in links:
                linked = by_id.get(_int(link))
                if linked is not None:
                    faces.append(linked)
        type_line = _arena_type_line(card, localization)
        face_records = tuple(
            {
                "name": _arena_name(face, localization),
                "type_line": _arena_type_line(face, localization),
                "oracle_text": _text(face.get("oracle_text", face.get("oracleText", face.get("text")))),
                "mana_cost": _mana_cost(face.get("castingcost", face.get("castingCost", face.get("manaCost")))),
                "colors": _colors(face.get("colors", face.get("colorIdentity", []))),
                "power": _text(face.get("power")),
                "toughness": _text(face.get("toughness")),
            }
            for face in faces
        )
        power = _text(card.get("power"))
        if power is None and len(face_records) > 1:
            power = _shared_face_text(face_records, "power")
        toughness = _text(card.get("toughness"))
        if toughness is None and len(face_records) > 1:
            toughness = _shared_face_text(face_records, "toughness")
        normalized.append(
            {
                "grp_id": grp_id,
                "name": _arena_name(card, localization),
                "colors": _colors(card.get("colors", card.get("colorIdentity", []))),
                "mana_value": _float(card.get("cmc", card.get("manaValue"))),
                "mana_cost": _mana_cost(card.get("castingcost", card.get("castingCost", card.get("manaCost")))),
                "rarity": _arena_rarity(card.get("rarity")),
                "type_line": type_line,
                "oracle_text": _text(card.get("oracle_text", card.get("oracleText", card.get("text")))),
                "produced_mana": _arena_produced_mana(
                    card, type_line=type_line
                ),
                "power": power,
                "toughness": toughness,
                "faces": face_records,
            }
        )
    return tuple(normalized)


def _arena_rarity(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip().lower().replace("mythic rare", "mythic") or None
    parsed = _int(value)
    return ARENA_RARITY_ID_MAP.get(parsed) if parsed is not None else None


@dataclass(frozen=True, slots=True)
class _SourceIndexes:
    arena: tuple[Mapping[str, Any], ...]
    arena_by_id: Mapping[int, tuple[Mapping[str, Any], ...]]
    mtg_by_scryfall_id: Mapping[str, tuple[tuple[int, Mapping[str, Any]], ...]]
    mtg_by_oracle_id: Mapping[str, tuple[tuple[int, Mapping[str, Any]], ...]]


def _build_source_indexes(
    arena_records: Iterable[Mapping[str, Any]],
    mtgjson_records: Iterable[Mapping[str, Any]],
) -> _SourceIndexes:
    arena = _arena_records(tuple(arena_records))
    arena_by_id_lists: dict[int, list[Mapping[str, Any]]] = {}
    for item in arena:
        arena_id = _int(item.get("grp_id"))
        if arena_id is not None:
            arena_by_id_lists.setdefault(arena_id, []).append(item)

    mtgjson = tuple(mtgjson_records)
    mtg_by_scryfall_id_lists: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    mtg_by_oracle_id_lists: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, item in enumerate(mtgjson):
        identifiers = item.get("identifiers")
        if isinstance(identifiers, dict):
            scryfall_id = identifiers.get("scryfallId")
            oracle_id = identifiers.get("scryfallOracleId")
            if isinstance(scryfall_id, str) and scryfall_id:
                mtg_by_scryfall_id_lists.setdefault(scryfall_id, []).append((index, item))
            if isinstance(oracle_id, str) and oracle_id:
                mtg_by_oracle_id_lists.setdefault(oracle_id, []).append((index, item))
    return _SourceIndexes(
        arena=arena,
        arena_by_id={name: tuple(items) for name, items in arena_by_id_lists.items()},
        mtg_by_scryfall_id={
            identifier: tuple(items) for identifier, items in mtg_by_scryfall_id_lists.items()
        },
        mtg_by_oracle_id={
            identifier: tuple(items) for identifier, items in mtg_by_oracle_id_lists.items()
        },
    )


def _mtgjson_arena_id(card: Mapping[str, Any] | None) -> int | None:
    if card is None:
        return None
    identifiers = card.get("identifiers")
    return _int(identifiers.get("mtgArenaId")) if isinstance(identifiers, dict) else None


def _match_arena(
    card: Mapping[str, Any],
    indexes: _SourceIndexes,
    *,
    mtgjson: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    arena_id = _int(card.get("arena_id"))
    if arena_id is None:
        arena_id = _mtgjson_arena_id(mtgjson)
    if arena_id is None:
        return None
    matches = indexes.arena_by_id.get(arena_id, ())
    return matches[0] if len(matches) == 1 else None


def _mtgjson_match_details(
    card: Mapping[str, Any], indexes: _SourceIndexes
) -> tuple[Mapping[str, Any] | None, str | None]:
    printing_id = _text(card.get("id"))
    if printing_id:
        exact_matches = indexes.mtg_by_scryfall_id.get(printing_id, ())
        if len(exact_matches) == 1:
            return exact_matches[0][1], None
        if len(exact_matches) > 1:
            return None, "ambiguous_mtgjson_scryfall_id"

    oracle_id = _text(card.get("oracle_id"))
    if not oracle_id:
        return None, None
    candidates = indexes.mtg_by_oracle_id.get(oracle_id, ())
    if not candidates:
        return None, None

    wanted_set = _normal_identity(card.get("set"))
    wanted_number = _normal_identity(card.get("collector_number"))
    if not wanted_set or not wanted_number:
        return None, "ambiguous_mtgjson_oracle_identity"
    narrowed = tuple(
        item
        for item in candidates
        if _normal_identity(item[1].get("setCode")) == wanted_set
        and _normal_identity(item[1].get("number")) == wanted_number
    )
    if len(narrowed) == 1:
        return narrowed[0][1], None
    return None, "ambiguous_mtgjson_oracle_identity"


def _match_mtgjson(card: Mapping[str, Any], indexes: _SourceIndexes) -> Mapping[str, Any] | None:
    return _mtgjson_match_details(card, indexes)[0]


def _field(card: Mapping[str, Any], mtg: Mapping[str, Any] | None, *names: str) -> Any:
    for name in names:
        value = card.get(name)
        if value is not None and value != "":
            return value
    if mtg is not None:
        for name in names:
            value = mtg.get(_MTGJSON_NAMES.get(name, name))
            if value is not None and value != "":
                return value
    return None


_MTGJSON_NAMES = {
    "oracle_text": "text",
    "type_line": "type",
    "mana_value": "manaValue",
    "mana_cost": "manaCost",
    "produced_mana": "producedMana",
    "collector_number": "number",
}
_DISAGREEMENT_FIELDS = (
    ("name", ("name",)),
    ("oracle_text", ("oracle_text", "text")),
    ("type_line", ("type_line", "type")),
    ("colors", ("colors",)),
    ("mana_cost", ("mana_cost", "manaCost")),
    ("mana_value", ("mana_value", "manaValue", "cmc")),
    ("power", ("power",)),
    ("toughness", ("toughness",)),
    ("rarity", ("rarity",)),
    ("produced_mana", ("produced_mana", "producedMana")),
)


def _source_value(
    source_name: str, source_card: Mapping[str, Any], aliases: Sequence[str]
) -> Any:
    if source_name == "mtgjson":
        return _field({}, source_card, *aliases)
    return _field(source_card, None, *aliases)


def _comparable_source_value(field_name: str, source_value: Any, canonical: Any) -> Any:
    if field_name in {"colors", "produced_mana"}:
        return _colors(source_value)
    if field_name == "mana_cost":
        return _mana_cost(source_value)
    if field_name == "mana_value":
        return _float(source_value)
    if isinstance(canonical, str) or field_name in {
        "name",
        "oracle_text",
        "type_line",
        "rarity",
    }:
        return _text(source_value)
    return source_value


def _source_disagreements(
    fields: Mapping[str, Any],
    *,
    arena_card: Mapping[str, Any] | None,
    mtgjson: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    disagreements: list[dict[str, Any]] = []
    for source_name, source_card in (("arena", arena_card), ("mtgjson", mtgjson)):
        if not source_card:
            continue
        for field_name, aliases in _DISAGREEMENT_FIELDS:
            source_value = _source_value(source_name, source_card, aliases)
            if source_value is None or fields[field_name] is None:
                continue
            canonical = fields[field_name]
            comparable = _comparable_source_value(field_name, source_value, canonical)
            if comparable != canonical:
                disagreements.append(
                    {
                        "field": field_name,
                        "source": source_name,
                        "canonical": canonical,
                        "source_value": comparable,
                    }
                )
    return disagreements


def _face_record(
    face: Mapping[str, Any], mtg_face: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    type_line, subtypes = _type_line_parts(_text(_field(face, mtg_face, "type_line")))
    color_value = _field(face, mtg_face, "colors")
    return {
        "name": _text(_field(face, mtg_face, "name")),
        "oracle_text": _text(_field(face, mtg_face, "oracle_text")),
        "keywords": _ordered_strings(_field(face, mtg_face, "keywords")),
        "type_line": type_line,
        "subtypes": subtypes,
        "colors": _color_field(color_value),
        "mana_cost": _mana_cost(_field(face, mtg_face, "mana_cost")),
        "mana_value": _float(_field(face, mtg_face, "mana_value", "cmc")),
        "produced_mana": _colors(_field(face, mtg_face, "produced_mana")),
        "power": _text(_field(face, mtg_face, "power")),
        "toughness": _text(_field(face, mtg_face, "toughness")),
    }


def _field_covered(row: Mapping[str, Any], field_name: str) -> bool:
    faces = row.get("faces")
    if field_name in {"oracle_text", "type_line"}:
        if isinstance(faces, (list, tuple)) and faces:
            return all(
                isinstance(face, dict) and face.get(field_name) is not None
                for face in faces
            )
    return row.get(field_name) is not None


def normalize_card(
    card: Mapping[str, Any],
    *,
    arena_records: Iterable[Mapping[str, Any]] = (),
    mtgjson_records: Iterable[Mapping[str, Any]] = (),
    provenance: Mapping[str, Mapping[str, Any]] | None = None,
    _indexes: _SourceIndexes | None = None,
) -> dict[str, Any]:
    """Normalize one Scryfall card while retaining source disagreements."""

    indexes = _indexes or _build_source_indexes(arena_records, mtgjson_records)
    mtgjson, mtgjson_match_reason = _mtgjson_match_details(card, indexes)
    arena_card = _match_arena(card, indexes, mtgjson=mtgjson)
    scryfall_arena_id = _int(card.get("arena_id"))
    local_arena_id = _int(arena_card.get("grp_id")) if arena_card else None
    mtgjson_arena_id = _mtgjson_arena_id(mtgjson)
    arena_identity = scryfall_arena_id
    if arena_identity is None:
        arena_identity = local_arena_id if local_arena_id is not None else mtgjson_arena_id
    layout = _text(card.get("layout")) or "unknown"
    scryfall_faces = card.get("card_faces")
    faces = tuple(face for face in scryfall_faces if isinstance(face, dict)) if isinstance(scryfall_faces, list) else ()
    mtg_faces = mtgjson.get("card_faces", ()) if isinstance(mtgjson, dict) else ()
    normalized_faces = tuple(
        _face_record(
            face,
            mtg_faces[index]
            if isinstance(mtg_faces, list)
            and index < len(mtg_faces)
            and isinstance(mtg_faces[index], dict)
            else None,
        )
        for index, face in enumerate(faces)
    )
    type_line, subtypes = _type_line_parts(_text(_field(card, mtgjson, "type_line")))
    color_value = _field(card, mtgjson, "colors")
    canonical_produced_mana = _field(card, mtgjson, "produced_mana")
    fields: dict[str, Any] = {
        "name": _text(card.get("name")),
        "arena_id": arena_identity,
        "grp_id": arena_identity,
        "set": _text(card.get("set")),
        "collector_number": _text(card.get("collector_number")),
        "released_at": _text(card.get("released_at")),
        "oracle_id": _text(card.get("oracle_id")),
        "oracle_text": _text(_field(card, mtgjson, "oracle_text")),
        "keywords": _ordered_strings(_field(card, mtgjson, "keywords")),
        "type_line": type_line,
        "subtypes": subtypes,
        "layout": layout,
        "faces": list(normalized_faces),
        "colors": _color_field(color_value),
        "mana_cost": _mana_cost(_field(card, mtgjson, "mana_cost")),
        "mana_value": _float(_field(card, mtgjson, "mana_value", "cmc")),
        "power": _text(_field(card, mtgjson, "power")),
        "produced_mana": (
            _colors(canonical_produced_mana)
            if canonical_produced_mana is not None
            else None
        ),
        "rarity": _text(_field(card, mtgjson, "rarity")),
        "toughness": _text(_field(card, mtgjson, "toughness")),
    }
    if arena_card:
        for field_name in (
            "oracle_text",
            "type_line",
            "mana_cost",
            "mana_value",
            "power",
            "rarity",
            "toughness",
        ):
            if fields[field_name] is None:
                fields[field_name] = arena_card.get(field_name)
        if fields["colors"] is None:
            fields["colors"] = arena_card.get("colors")
        if fields["produced_mana"] is None:
            fields["produced_mana"] = arena_card.get("produced_mana")
    if fields["power"] is None and len(normalized_faces) > 1:
        fields["power"] = _shared_face_text(normalized_faces, "power")
    if fields["toughness"] is None and len(normalized_faces) > 1:
        fields["toughness"] = _shared_face_text(normalized_faces, "toughness")
    if not fields["colors"] and normalized_faces:
        fields["colors"] = _colors(
            [color for face in normalized_faces for color in (face["colors"] or [])]
        )
    if fields["mana_cost"] is None and normalized_faces:
        fields["mana_cost"] = (
            " // ".join(face["mana_cost"] for face in normalized_faces if face["mana_cost"]) or None
        )
    if fields["produced_mana"] is None and normalized_faces:
        fields["produced_mana"] = _colors(
            [color for face in normalized_faces for color in (face["produced_mana"] or [])]
        )
    if fields["produced_mana"] is None:
        fields["produced_mana"] = []
    if fields["type_line"] is None and normalized_faces:
        fields["type_line"] = (
            " // ".join(face["type_line"] for face in normalized_faces if face["type_line"]) or None
        )
    fields["subtypes"] = (
        _type_line_parts(fields["type_line"])[1]
        if isinstance(fields["type_line"], str) and " // " not in fields["type_line"]
        else list(dict.fromkeys(subtype for face in normalized_faces for subtype in face["subtypes"]))
    )
    disagreements = _source_disagreements(
        fields,
        arena_card=arena_card,
        mtgjson=mtgjson,
    )
    source_provenance: dict[str, Mapping[str, Any]] = dict(provenance or {})
    if arena_card:
        source_provenance.setdefault("arena", {"grp_id": arena_card.get("grp_id"), "name": arena_card.get("name")})
    if mtgjson:
        source_provenance.setdefault(
            "mtgjson",
            {
                "uuid": mtgjson.get("uuid"),
                "set_code": mtgjson.get("setCode"),
                "number": mtgjson.get("number"),
                "mtg_arena_id": mtgjson_arena_id,
            },
        )
    source_provenance.setdefault("scryfall", {"id": card.get("id"), "oracle_id": card.get("oracle_id")})
    fields["source_provenance"] = source_provenance
    fields["source_disagreements"] = disagreements
    missing = [
        field_name
        for field_name in SEMANTIC_FIELDS
        if not _field_covered(fields, field_name)
        or (field_name != "colors" and fields[field_name] == "unknown")
    ]
    if mtgjson_match_reason:
        missing.append(mtgjson_match_reason)
    disagreement_reasons = [
        f"source_disagreement:{item['field']}"
        for item in disagreements
        if isinstance(item.get("field"), str)
    ]
    unsafe_reasons = list(
        dict.fromkeys(
            missing
            + (
                [f"unsupported_layout:{layout}"]
                if layout not in SUPPORTED_LAYOUTS
                else []
            )
            + disagreement_reasons
        )
    )
    fields["unsafe_to_classify"] = bool(unsafe_reasons)
    fields["unsafe_reasons"] = unsafe_reasons
    return fields


def normalize_cards(
    cards: Iterable[Mapping[str, Any]],
    *,
    arena_records: Iterable[Mapping[str, Any]] = (),
    mtgjson_records: Iterable[Mapping[str, Any]] = (),
    provenance: Mapping[str, Mapping[str, Any]] | None = None,
    _indexes: _SourceIndexes | None = None,
) -> tuple[dict[str, Any], ...]:
    indexes = _indexes or _build_source_indexes(arena_records, mtgjson_records)
    return tuple(
        sorted(
            (
                normalize_card(card, provenance=provenance, _indexes=indexes)
                for card in cards
            ),
            key=_card_sort_key,
        )
    )


def _matches_broad_selection(card: Mapping[str, Any]) -> bool:
    set_code = _normal_name(_text(card.get("set")))
    release = _text(card.get("released_at")) or _text(card.get("release_date"))
    arena_era = bool(release and release[:4].isdigit() and int(release[:4]) >= 2018)
    return (
        set_code in {"hob", "hbl"}
        or arena_era
        or bool(card.get("card_faces"))
        or card.get("layout") in SUPPORTED_LAYOUTS
    )


def _selection_sort_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _normal_name(_text(row.get("set"))),
        _normal_name(_text(row.get("collector_number"))),
        _normal_name(_text(row.get("name"))),
    )


def _coverage_oracle_text(row: Mapping[str, Any]) -> str:
    oracle_texts = [_text(row.get("oracle_text")) or ""]
    faces = row.get("faces")
    if isinstance(faces, (list, tuple)):
        oracle_texts.extend(
            _text(face.get("oracle_text")) or ""
            for face in faces
            if isinstance(face, dict)
        )
    return "\n".join(oracle_texts)


def select_cards(cards: Iterable[Mapping[str, Any]], selection: SelectionSpec | None = None) -> SelectionResult:
    """Select cards and return inspectable, deterministic selection metadata."""

    spec = selection or SelectionSpec()
    candidates = tuple(cards)
    selected: list[Mapping[str, Any]] = []
    for card in candidates:
        if spec.mode == "explicit":
            include = _normal_name(_text(card.get("set"))) in spec.sets
        else:
            include = _matches_broad_selection(card)
        if include:
            selected.append(card)
    selected.sort(key=_selection_sort_key)
    metadata = {
        **spec.to_json(),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "selected_sets": sorted({_text(item.get("set")) for item in selected if _text(item.get("set"))}),
    }
    return SelectionResult(cards=tuple(selected), metadata=metadata)


def build_coverage_report(rows: Iterable[Mapping[str, Any]], *, selection: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic field/layout/mechanic/source coverage metrics."""

    ordered_rows = tuple(sorted(rows, key=_card_sort_key))
    missing_fields = {
        field_name: sum(
            1
            for row in ordered_rows
            if not _field_covered(row, field_name)
            or (field_name != "colors" and row.get(field_name) == "unknown")
        )
        for field_name in SEMANTIC_FIELDS
    }
    unsupported: dict[str, int] = {}
    patterns: dict[str, int] = {name: 0 for name, _ in WORDING_PATTERNS}
    disagreement_details: list[dict[str, Any]] = []
    unsafe_details: list[dict[str, Any]] = []
    for row in ordered_rows:
        layout = _text(row.get("layout")) or "unknown"
        if layout not in SUPPORTED_LAYOUTS:
            unsupported[layout] = unsupported.get(layout, 0) + 1
        oracle = _coverage_oracle_text(row)
        for name, pattern in WORDING_PATTERNS:
            if pattern.search(oracle):
                patterns[name] = patterns.get(name, 0) + 1
        disagreements = row.get("source_disagreements")
        if isinstance(disagreements, list):
            disagreement_details.extend(item for item in disagreements if isinstance(item, dict))
        if row.get("unsafe_to_classify"):
            unsafe_details.append({"name": row.get("name"), "set": row.get("set"), "reasons": row.get("unsafe_reasons", [])})
    missing_arena_ids = sum(1 for row in ordered_rows if row.get("arena_id") is None)
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "selection": dict(selection or {}),
        "total_cards": len(ordered_rows),
        "missing_arena_ids": missing_arena_ids,
        "missing_semantic_fields": missing_fields,
        "unsupported_layouts": dict(sorted(unsupported.items())),
        "unsupported_layout_count": sum(unsupported.values()),
        "wording_mechanic_patterns": dict(sorted(patterns.items())),
        "source_disagreements": len(disagreement_details),
        "unsafe_to_classify": len(unsafe_details),
        "details": {
            "source_disagreements": disagreement_details,
            "unsafe_to_classify": unsafe_details,
        },
    }


def write_normalized_rows(rows: Iterable[Mapping[str, Any]], path: PathInput) -> Path:
    """Write stable compact JSONL; no retrieval timestamp is accepted in rows."""

    ordered = sorted(rows, key=_card_sort_key)
    payload = b"".join(_json_bytes(dict(row)) for row in ordered)
    output = Path(path)
    _atomic_write(output, payload)
    return output


def write_coverage_report(report: Mapping[str, Any], path: PathInput) -> Path:
    output = Path(path)
    _atomic_write(output, _json_bytes(dict(report), indent=2))
    return output


def iter_normalized_rows(path: PathInput) -> Iterator[Mapping[str, Any]]:
    """Yield normalized rows offline; this function never invokes acquisition/network code."""

    input_path = Path(path)
    try:
        stream = _open_bulk(input_path)
    except OSError as error:
        raise CorpusOfflineError(f"Could not open normalized corpus {input_path}: {error}.") from error
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise CorpusError(f"Malformed normalized JSONL {input_path}:{line_number}: {error}.") from error
            if not isinstance(row, dict):
                raise CorpusError(f"Normalized corpus row {line_number} is not an object.")
            yield row


def load_normalized_rows(path: PathInput) -> tuple[Mapping[str, Any], ...]:
    return tuple(iter_normalized_rows(path))


def build_corpus(
    *,
    source_specs: Iterable[SourceSpec],
    cache_dir: PathInput = DEFAULT_CACHE_DIR,
    output_dir: PathInput = DEFAULT_ARTIFACT_DIR,
    selection: SelectionSpec | None = None,
    lock_path: PathInput | None = None,
    offline: bool = False,
    timeout_seconds: int = 60,
) -> tuple[Path, Path, AcquisitionResult, SelectionResult]:
    """Acquire, normalize, select, and emit deterministic corpus artifacts."""

    acquisition = acquire_sources(
        source_specs=source_specs,
        cache_dir=cache_dir,
        lock_path=lock_path,
        offline=offline,
        timeout_seconds=timeout_seconds,
    )
    arena_sources = tuple(source for source in acquisition.sources if source.spec.kind == "arena")
    if arena_sources and len(arena_sources) != 2:
        raise CorpusError(
            "Arena mapping inputs are incomplete; provide both data_cards and data_loc sources."
        )
    mtgjson_sources = tuple(
        source for source in acquisition.sources if source.spec.kind == "mtgjson"
    )
    scryfall, arena_raw, mtgjson = _source_cards(acquisition.sources)
    if mtgjson_sources and any(source.spec.required for source in mtgjson_sources) and not mtgjson:
        raise CorpusError("A required MTGJSON mapping source produced no cards.")
    indexes = _build_source_indexes(arena_raw, mtgjson)
    if arena_sources and not indexes.arena:
        raise CorpusError("Required Arena mapping inputs produced no card records.")
    normalized = normalize_cards(
        scryfall,
        provenance={
            source.spec.name: {"sha256": source.sha256, "url": source.spec.url}
            for source in acquisition.sources
        },
        _indexes=indexes,
    )
    if not normalized:
        raise CorpusError("A required Scryfall card source produced no cards.")
    selected_raw = select_cards(normalized, selection=selection)
    output = Path(output_dir)
    normalized_path = write_normalized_rows(
        selected_raw.cards, output / NORMALIZED_FILENAME
    )
    report_path = write_coverage_report(
        build_coverage_report(
            selected_raw.cards, selection=selected_raw.metadata
        ),
        output / REPORT_FILENAME,
    )
    return normalized_path, report_path, acquisition, selected_raw
