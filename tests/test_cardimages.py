from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from types import TracebackType

import pytest

from draftgoblin.carddb import CardDatabase, CardInfo
from draftgoblin.cardimages import CardImageError, CardImageService


class _ImageResponse:
    def __init__(self, *, image_data: bytes) -> None:
        self.image_data = image_data

    def __enter__(self) -> _ImageResponse:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    def read(self, limit: int) -> bytes:
        return self.image_data[:limit]


def test_card_image_service_resolves_direct_and_name_indexed_urls(
    tmp_path: Path,
) -> None:
    fallback_card = _card(grp_id=2, name="Fallback Card")
    database = CardDatabase(
        cards={fallback_card.grp_id: fallback_card},
        image_uris_by_name={
            "fallback card": "https://cards.example/fallback.webp",
        },
    )
    service = CardImageService(cache_dir=tmp_path)

    assert service.resolve_image_uri(
        card=_card(
            grp_id=1,
            name="Direct Card",
            image_uri="https://cards.example/direct.png",
        ),
        card_database=database,
    ) == "https://cards.example/direct.png"
    assert service.resolve_image_uri(
        card=fallback_card,
        card_database=database,
    ) == "https://cards.example/fallback.webp"
    assert service.resolve_image_uri(
        card=CardInfo.unknown_card(grp_id=3),
        card_database=database,
    ) is None


def test_card_image_service_retries_then_caches_successful_download(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, float]] = []

    def opener(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _ImageResponse:
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            raise urllib.error.URLError("temporary network failure")

        return _ImageResponse(image_data=b"fixture image")

    service = CardImageService(
        cache_dir=tmp_path,
        max_attempts=2,
        opener=opener,
    )

    image_path = service.fetch(image_uri="https://cards.example/card.png?size=normal")

    assert calls == [
        ("https://cards.example/card.png?size=normal", 10.0),
        ("https://cards.example/card.png?size=normal", 10.0),
    ]
    assert image_path == service.cached_path(
        image_uri="https://cards.example/card.png?size=normal"
    )
    assert image_path.suffix == ".png"
    assert image_path.read_bytes() == b"fixture image"

    assert service.fetch(
        image_uri="https://cards.example/card.png?size=normal"
    ) == image_path
    assert len(calls) == 2


def test_card_image_service_rejects_oversized_response_without_caching(
    tmp_path: Path,
) -> None:
    service = CardImageService(
        cache_dir=tmp_path,
        max_bytes=3,
        opener=lambda request, timeout: _ImageResponse(image_data=b"four"),
    )

    with pytest.raises(CardImageError, match="response too large"):
        service.fetch(image_uri="https://cards.example/card.jpg")

    assert not service.cached_path(
        image_uri="https://cards.example/card.jpg"
    ).exists()


def test_card_image_service_reports_exhausted_retries(
    tmp_path: Path,
) -> None:
    calls = 0

    def opener(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _ImageResponse:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("offline")

    service = CardImageService(
        cache_dir=tmp_path,
        max_attempts=2,
        opener=opener,
    )

    with pytest.raises(
        CardImageError,
        match="Image fetch failed after 2 attempts",
    ):
        service.fetch(image_uri="https://cards.example/card.jpg")

    assert calls == 2


def _card(
    *,
    grp_id: int,
    name: str,
    image_uri: str | None = None,
) -> CardInfo:
    return CardInfo(
        grp_id=grp_id,
        name=name,
        colors=("W",),
        mana_value=2.0,
        rarity="common",
        types=("Creature",),
        image_uri=image_uri,
    )

