"""Resolve, download, and cache card images for any frontend.
Network and filesystem behavior stay behind a UI-neutral Python service.
"""

from __future__ import annotations

import hashlib
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any, TypeAlias

from draftgoblin.carddb import SCRYFALL_USER_AGENT, CardDatabase, CardInfo
from draftgoblin.paths import app_data_dir

PathInput: TypeAlias = str | PathLike[str]
ImageUrlOpener: TypeAlias = Callable[..., Any]

CARD_IMAGE_CACHE_DIR_NAME = "card-images"
CARD_IMAGE_MAX_BYTES = 8 * 1024 * 1024
CARD_IMAGE_TIMEOUT_SECONDS = 10.0
CARD_IMAGE_MAX_ATTEMPTS = 2
CARD_IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class CardImageError(RuntimeError):
    """Raised when a card image cannot be resolved or cached.
    Frontends decide how and where to present the failure.
    """


@dataclass(frozen=True, slots=True)
class CardImageService:
    """Provide bounded card-image resolution, download, and caching.
    Failed downloads can be retried without retaining frontend state.
    """

    cache_dir: Path
    max_bytes: int = CARD_IMAGE_MAX_BYTES
    timeout_seconds: float = CARD_IMAGE_TIMEOUT_SECONDS
    max_attempts: int = CARD_IMAGE_MAX_ATTEMPTS
    opener: ImageUrlOpener = field(
        default=urllib.request.urlopen,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("Card-image byte limit must be positive.")
        if self.timeout_seconds <= 0:
            raise ValueError("Card-image timeout must be positive.")
        if self.max_attempts <= 0:
            raise ValueError("Card-image attempt limit must be positive.")

    def resolve_image_uri(
        self,
        *,
        card: CardInfo,
        card_database: CardDatabase,
    ) -> str | None:
        """Resolve a card's direct or name-indexed Scryfall image URL.
        Unknown cards remain unresolved rather than triggering network work.
        """

        if card.image_uri is not None:
            return card.image_uri
        if card.unknown:
            return None

        return card_database.image_uri_for_name(name=card.name)

    def cached_path(self, *, image_uri: str) -> Path:
        """Return the deterministic cache path for one image URL.
        Existing and future downloads share the same collision-resistant key.
        """

        digest = hashlib.sha256(image_uri.encode("utf-8")).hexdigest()
        extension = _card_image_extension(image_uri=image_uri)
        return self.cache_dir / f"{digest}{extension}"

    def fetch(self, *, image_uri: str) -> Path:
        """Return a cached image or download it with bounded retries.
        Successful writes are atomic and oversized responses are rejected.
        """

        image_path = self.cached_path(image_uri=image_uri)
        if image_path.is_file():
            return image_path

        request = urllib.request.Request(
            image_uri,
            headers={
                "Accept": "image/*,*/*;q=0.8",
                "User-Agent": SCRYFALL_USER_AGENT,
            },
        )
        last_error: OSError | urllib.error.URLError | None = None
        for _ in range(self.max_attempts):
            try:
                with self.opener(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    image_data = response.read(self.max_bytes + 1)
            except (OSError, urllib.error.URLError) as error:
                last_error = error
                continue

            if len(image_data) > self.max_bytes:
                raise CardImageError("Image fetch failed: response too large.")

            return self._write_image(image_path=image_path, image_data=image_data)

        detail = "unknown network error" if last_error is None else str(last_error)
        raise CardImageError(
            f"Image fetch failed after {self.max_attempts} attempts: {detail}"
        ) from last_error

    def _write_image(self, *, image_path: Path, image_data: bytes) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.cache_dir,
                delete=False,
            ) as temporary_file:
                temporary_file.write(image_data)
                temporary_path = Path(temporary_file.name)

            temporary_path.replace(image_path)
        except OSError as error:
            raise CardImageError(f"Image cache write failed: {error}") from error
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

        return image_path


def card_image_cache_dir(*, app_dir: PathInput | None = None) -> Path:
    """Return the shared card-image cache directory.
    An explicit application directory keeps tests and portable runs isolated.
    """

    root = Path(app_data_dir() if app_dir is None else app_dir)
    return root / CARD_IMAGE_CACHE_DIR_NAME


def _card_image_extension(*, image_uri: str) -> str:
    parsed_uri = urllib.parse.urlparse(image_uri)
    extension = Path(parsed_uri.path).suffix.lower()
    if extension in CARD_IMAGE_FILE_EXTENSIONS:
        return extension

    return ".jpg"

