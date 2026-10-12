"""M8: the homepage's game list and the Steam store search proxy."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.games import service
from src.games.constants import AspectStatus
from src.games.models import Game
from src.reviews import constants
from src.reviews import service as reviews_service
from src.reviews.models import Review, ReviewAspect

DONE_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
SEARCH = {
    "total": 3,
    "items": [
        {"type": "app", "name": "Hades", "id": 1145360, "tiny_image": "https://img/hades.jpg"},
        {"type": "sub", "name": "Hades Bundle", "id": 5},  # not an app
        {"type": "app", "name": "Hades II", "id": 1145350},
    ],
}


@pytest.fixture(autouse=True)
def _empty_search_cache() -> None:
    service._search_cache.clear()


async def test_lists_only_done_games_with_counts(client, engine):
    async with AsyncSession(engine) as session:
        session.add_all([
            Game(appid=2, name="Beta", aspects_status=AspectStatus.DONE,
                 aspects_processed_at=DONE_AT, last_ingested_at=DONE_AT),
            Game(appid=1, name="Alpha", aspects_status=AspectStatus.DONE,
                 aspects_processed_at=DONE_AT, last_ingested_at=DONE_AT),
            Game(appid=3, name="Pending", last_ingested_at=DONE_AT),
        ])
        await session.flush()
        session.add_all(
            Review(id=i, appid=1, review_text="x", voted_up=i < 2, votes_up=0, playtime_forever=0,
                   language="english", created_at=DONE_AT,
                   predicted_sentiment="positive" if i < 2 else "negative")
            for i in range(3)
        )
        # Not analyzed (no predicted sentiment): left out of steam_sample, like /aspects' overall.
        session.add(Review(id=3, appid=1, review_text="x", voted_up=False, votes_up=0,
                           playtime_forever=0, language="english", created_at=DONE_AT))
        await session.flush()
        session.add(ReviewAspect(review_id=2, sentence_text="it crashes", aspect_label="bugs",
                                 similarity_score=0.5))
        await session.commit()
    # What the run that processed the game stores (`process_reviews`); the list only reads it.
    async with AsyncSession(engine) as session:
        summary = await reviews_service._summary_counts(session, 1)
        await session.execute(update(Game).where(Game.appid == 1).values(
            card_counts={"aspects": summary["aspects"], "steam_sample": summary["steam_sample"]}))
        await session.commit()

    resp = await client.get("/games")
    assert resp.status_code == 200
    alpha, beta = resp.json()
    assert {k: alpha[k] for k in ("appid", "name", "review_count", "analyzed_at")} == {
        "appid": 1, "name": "Alpha", "review_count": 4, "analyzed_at": "2026-10-01T12:00:00Z",
    }
    assert (beta["name"], beta["review_count"]) == ("Beta", 0)
    # The card's comparison inputs: Steam's thumbs on the sample and the per-aspect counts.
    assert alpha["steam_sample"] == {"positive": 2, "negative": 1, "total": 3, "positive_pct": 66.7}
    bugs = next(a for a in alpha["aspects"] if a["aspect"] == "bugs")
    assert bugs == {"aspect": "bugs", "positive": 0, "negative": 1, "total": 1, "positive_pct": 0.0}
    assert beta["steam_sample"]["total"] == 0


async def test_search_maps_apps_and_caches_per_term(client, steam):
    route = steam.get(constants.STORESEARCH_URL).mock(
        return_value=httpx.Response(200, json=SEARCH)
    )
    first = await client.get("/steam/search", params={"q": "Hades"})
    assert first.status_code == 200
    assert first.json() == [
        {"appid": 1145360, "name": "Hades", "image": "https://img/hades.jpg",
         "availability": "on_demand"},
        {"appid": 1145350, "name": "Hades II", "image": None, "availability": "on_demand"},
    ]
    assert route.calls.last.request.url.params["term"] == "hades"
    assert (await client.get("/steam/search", params={"q": " hades "})).json() == first.json()
    assert route.call_count == 1  # same term within the TTL: served from the cache

    await client.get("/steam/search", params={"q": "portal"})
    assert route.call_count == 2


async def test_search_says_which_results_open_here(client, engine, steam, monkeypatch):
    steam.get(constants.STORESEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH))
    async with AsyncSession(engine) as session:
        session.add(Game(appid=1145360, name="Hades", aspects_status=AspectStatus.DONE))
        await session.commit()
    monkeypatch.setattr(settings, "allow_on_demand_processing", False)

    results = (await client.get("/steam/search", params={"q": "hades"})).json()
    assert [r["availability"] for r in results] == ["analyzed", "unavailable"]

    # Ingested and being rerun: /aspects answers 202 rather than 403, so it opens.
    async with AsyncSession(engine) as session:
        session.add(Game(appid=1145350, name="Hades II", last_ingested_at=DONE_AT,
                         aspects_status=AspectStatus.PROCESSING))
        await session.commit()
    results = (await client.get("/steam/search", params={"q": "hades"})).json()
    assert [r["availability"] for r in results] == ["analyzed", "on_demand"]


async def test_search_rejects_short_terms_and_reports_steam_failure(client, steam):
    assert (await client.get("/steam/search", params={"q": "h"})).status_code == 422
    steam.get(constants.STORESEARCH_URL).mock(return_value=httpx.Response(503))
    resp = await client.get("/steam/search", params={"q": "hades"})
    assert resp.status_code == 502
    assert service._search_cache == {}  # a failure is not cached


async def test_a_hung_search_does_not_hold_up_other_terms(client, steam):
    release = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params["term"] == "slow":
            await release.wait()  # Steam hanging on this one term
        return httpx.Response(200, json=SEARCH)

    steam.get(constants.STORESEARCH_URL).mock(side_effect=answer)
    slow = asyncio.create_task(client.get("/steam/search", params={"q": "slow"}))
    await asyncio.sleep(0.05)
    fast = await asyncio.wait_for(client.get("/steam/search", params={"q": "fast"}), 2)
    assert fast.status_code == 200
    assert not slow.done()
    release.set()
    assert (await slow).status_code == 200
