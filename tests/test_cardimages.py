from __future__ import annotations

import http.client
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


class _IncompleteReadResponse(_ImageResponse):
    def read(self, limit: int) -> bytes:
        raise http.client.IncompleteRead(b'{"image_uris":', 100)


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


def test_focused_named_lookup_for_nighthowl_pursuer_is_positive_cached(
    tmp_path: Path,
) -> None:
    card = _card(grp_id=103454, name="Nighthowl Pursuer")
    database = CardDatabase(cards={card.grp_id: card})
    requests: list[urllib.request.Request] = []

    def metadata_opener(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _ImageResponse:
        requests.append(request)
        return _ImageResponse(
            image_data=(
                b'{"name":"Nighthowl Pursuer","image_uris":'
                b'{"normal":"https://cards.example/nighthowl.jpg"}}'
            )
        )

    service = CardImageService(
        cache_dir=tmp_path,
        metadata_opener=metadata_opener,
    )

    assert service.resolve_image_uri(card=card, card_database=database) is None
    assert service.resolve_focused_image_uri(
        card=card,
        card_database=database,
    ) == "https://cards.example/nighthowl.jpg"
    assert service.resolve_focused_image_uri(
        card=card,
        card_database=database,
    ) == "https://cards.example/nighthowl.jpg"

    assert len(requests) == 1
    assert requests[0].full_url == (
        "https://api.scryfall.com/cards/named?exact=Nighthowl%20Pursuer"
    )
    assert requests[0].get_header("Accept") == "application/json;q=0.9,*/*;q=0.8"
    assert requests[0].get_header("User-agent") is not None


def test_focused_named_lookup_rate_limits_distinct_cards(
    tmp_path: Path,
) -> None:
    card_one = _card(grp_id=1, name="First Card")
    card_two = _card(grp_id=2, name="Second Card")
    database = CardDatabase(cards={1: card_one, 2: card_two})
    sleeps: list[float] = []

    service = CardImageService(
        cache_dir=tmp_path,
        metadata_opener=lambda request, timeout: _ImageResponse(
            image_data=b'{"image_uris":{"normal":"https://cards.example/card.jpg"}}'
        ),
        monotonic_clock=lambda: 10.0,
        sleep=sleeps.append,
    )

    assert service.resolve_focused_image_uri(
        card=card_one,
        card_database=database,
    ) == "https://cards.example/card.jpg"
    assert service.resolve_focused_image_uri(
        card=card_two,
        card_database=database,
    ) == "https://cards.example/card.jpg"

    assert sleeps == [0.5]


def test_focused_named_lookup_treats_404_as_unavailable(
    tmp_path: Path,
) -> None:
    card = _card(grp_id=1, name="Missing Card")
    database = CardDatabase(cards={card.grp_id: card})

    def metadata_opener(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _ImageResponse:
        raise urllib.error.HTTPError(request.full_url, 404, "Not found", {}, None)

    service = CardImageService(
        cache_dir=tmp_path,
        metadata_opener=metadata_opener,
    )

    assert service.resolve_focused_image_uri(card=card, card_database=database) is None


def test_focused_named_lookup_wraps_incomplete_metadata_response(
    tmp_path: Path,
) -> None:
    card = _card(grp_id=1, name="Interrupted Card")
    database = CardDatabase(cards={card.grp_id: card})

    def metadata_opener(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _ImageResponse:
        return _IncompleteReadResponse(image_data=b"")

    service = CardImageService(
        cache_dir=tmp_path,
        metadata_opener=metadata_opener,
    )

    with pytest.raises(CardImageError, match="Card metadata lookup failed"):
        service.resolve_focused_image_uri(card=card, card_database=database)


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

