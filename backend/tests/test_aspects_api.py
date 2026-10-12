"""M5: the sentiment + aspect processing pass and the endpoints that read its results.

The ML functions are replaced with keyword fakes, so these tests need neither the trained
sentiment artifact nor the sentence-transformer download.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.games.constants import AspectStatus
from src.games.models import Game
from src.ml import aspects, sentiment
from src.reviews import constants, service
from src.reviews.constants import Skipped
from src.reviews.models import Review, ReviewAspect
from tests.steam_fakes import APPREVIEWS_RE

APPID = 1145360
TEXTS = {
    1: "It crashes a lot. Another crash today. Price is fair.",  # negative: bugs x2, price
    2: "Price is great. Love it.",  # positive: price
    3: "Just vibes. Love it.",  # positive: every unit "none"
    4: "Bad fps. Bad overall.",  # negative: performance
}
KEYWORDS = {"crash": "bugs", "price": "price", "fps": "performance"}
STEAM_SUMMARY = {
    "review_score_desc": "Very Positive", "total_positive": 900, "total_negative": 100,
    "total_reviews": 1000,
}


@pytest.fixture(autouse=True)
def steam_summary(steam):
    """Steam's store rating, which each processing run fetches once."""
    return steam.get(url__regex=APPREVIEWS_RE, params__contains={"num_per_page": "0"}).mock(
        return_value=httpx.Response(200, json={"success": 1, "query_summary": STEAM_SUMMARY})
    )


@pytest.fixture
def ml_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def predict(review_text: str) -> dict:
        calls.append(review_text)
        negative = "crash" in review_text or "Bad" in review_text
        return {"label": "negative" if negative else "positive", "confidence": 0.9}

    def assign_aspects(review_text: str) -> list[dict]:
        if review_text.startswith("[img]"):
            return []  # like the real splitter on BBCode-only text
        units = review_text.split(". ")
        tag = lambda unit: next((a for k, a in KEYWORDS.items() if k in unit.lower()), "none")
        return [{"sentence": u, "aspect": tag(u), "similarity": 0.5} for u in units]

    monkeypatch.setattr(sentiment, "predict", predict)
    # Quote filtering at the end of a run; not a per-review call, so not counted in `calls`.
    monkeypatch.setattr(sentiment, "predict_labels", lambda texts: [
        "negative" if "crash" in t or "Bad" in t else "positive" for t in texts
    ])
    monkeypatch.setattr(aspects, "assign_aspects", assign_aspects)
    monkeypatch.setattr(sentiment, "ensure_loaded", dict)
    return calls


@pytest.fixture
async def seeded(engine) -> None:
    """An ingested (not processed) game with TEXTS as reviews; review 4 is the newest."""
    base = datetime(2026, 9, 1, tzinfo=UTC)
    async with AsyncSession(engine) as session:
        session.add(Game(appid=APPID, name="Hades", last_ingested_at=base))
        await session.flush()
        session.add_all(
            Review(
                id=rid, appid=APPID, review_text=t, voted_up=True, votes_up=0,
                playtime_forever=0, language="english", created_at=base + timedelta(days=rid),
            )
            for rid, t in TEXTS.items()
        )
        await session.commit()


async def add_review(engine, rid: int, review_text: str) -> None:
    async with AsyncSession(engine) as session:
        session.add(Review(
            id=rid, appid=APPID, review_text=review_text, voted_up=True, votes_up=0,
            playtime_forever=0, language="english", created_at=datetime(2026, 8, 1, tzinfo=UTC),
        ))
        await session.commit()


async def set_status(engine, status: AspectStatus) -> None:
    async with engine.begin() as conn:
        await conn.execute(update(Game).values(aspects_status=status))


async def game_status(engine) -> tuple[str, str | None]:
    async with engine.connect() as conn:
        return (await conn.execute(select(Game.aspects_status, Game.aspects_error))).one()


async def processed_at(engine) -> datetime | None:
    async with engine.connect() as conn:
        return await conn.scalar(select(Game.aspects_processed_at))


async def process(engine, force: bool = False) -> int | Skipped:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await service.process_reviews(session, APPID, force=force)


async def unit_count(engine) -> int:
    async with engine.connect() as conn:
        return await conn.scalar(select(func.count()).select_from(ReviewAspect))


async def test_process_stores_units_with_review_sentiment(engine, seeded, ml_calls):
    assert await process(engine) == 4

    async with engine.connect() as conn:
        rows = (await conn.execute(select(ReviewAspect).order_by(ReviewAspect.id))).all()
        sentiments = dict((await conn.execute(select(Review.id, Review.predicted_sentiment))).all())
    assert await game_status(engine) == (AspectStatus.DONE, None)
    assert await processed_at(engine) is not None
    assert len(rows) == 9
    assert [r.aspect_label for r in rows if r.review_id == 1] == ["bugs", "bugs", "price"]
    assert {r.aspect_label for r in rows if r.review_id == 3} == {"none"}
    assert sentiments == {1: "negative", 2: "positive", 3: "positive", 4: "negative"}


async def test_rerun_is_a_noop_and_force_replaces(engine, seeded, ml_calls):
    await process(engine)
    calls_after_first = len(ml_calls)

    assert await process(engine) == Skipped.ALREADY_DONE
    assert len(ml_calls) == calls_after_first
    assert await unit_count(engine) == 9

    assert await process(engine, force=True) == 4
    assert await unit_count(engine) == 9


async def test_process_skips_a_game_another_worker_claimed(engine, seeded, ml_calls):
    await set_status(engine, AspectStatus.PROCESSING)
    assert await process(engine) == Skipped.INTERRUPTED
    assert ml_calls == []
    assert await unit_count(engine) == 0

    assert await process(engine, force=True) == 4  # takes over a worker that died
    assert (await game_status(engine))[0] == AspectStatus.DONE


async def test_startup_fails_interrupted_runs_only(engine, seeded, ml_calls):
    await set_status(engine, AspectStatus.PROCESSING)
    async with AsyncSession(engine) as session:
        session.add(Game(appid=7, name="Done", aspects_status=AspectStatus.DONE))
        await session.commit()

    async with AsyncSession(engine) as session:
        assert await service.fail_interrupted_runs(session) == 1
    async with engine.connect() as conn:
        rows = (await conn.execute(select(Game.appid, Game.aspects_status, Game.aspects_error))).all()
    assert sorted(rows) == [
        (7, AspectStatus.DONE, None),
        (APPID, AspectStatus.FAILED, constants.INTERRUPTED_ERROR),
    ]


async def test_a_live_run_is_neither_failed_at_startup_nor_taken_over(engine, seeded, ml_calls):
    # Another process (a CLI --force run, or a second API instance) is running this game.
    await set_status(engine, AspectStatus.PROCESSING)
    async with engine.connect() as other:
        await other.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :appid)"),
            {"ns": constants.PROCESS_LOCK_NAMESPACE, "appid": APPID},
        )
        async with AsyncSession(engine) as session:
            assert await service.fail_interrupted_runs(session) == 0
        assert await process(engine, force=True) == Skipped.RUNNING
        assert (await game_status(engine))[0] == AspectStatus.PROCESSING
        assert ml_calls == []

    # That process died: its lock is gone, so the game counts as interrupted.
    async with AsyncSession(engine) as session:
        assert await service.fail_interrupted_runs(session) == 1


async def test_checking_for_dead_runs_leaves_the_run_lock_free(engine, seeded, monkeypatch):
    # A poll checks a "processing" game while a --force run is starting: the check must not
    # hold the run lock, or the run skips itself as RUNNING with nothing running.
    await set_status(engine, AspectStatus.PROCESSING)
    lock_free_during_check = []
    async with AsyncSession(engine) as session:
        commit = session.commit

        async def commit_after_trying_the_lock():
            async with engine.connect() as run:
                lock_free_during_check.append(
                    await run.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:ns, :appid)"),
                        {"ns": constants.PROCESS_LOCK_NAMESPACE, "appid": APPID},
                    )
                )
            await commit()

        monkeypatch.setattr(session, "commit", commit_after_trying_the_lock)
        assert await service.fail_interrupted_runs(session, APPID) == 1
    assert lock_free_during_check == [True]


async def test_process_skips_unknown_app(engine, ml_calls):
    async with AsyncSession(engine) as session:
        assert await service.process_reviews(session, APPID) == Skipped.NOT_INGESTED


async def test_failure_is_recorded_and_needs_force(engine, seeded, ml_calls, monkeypatch):
    real_predict = sentiment.predict

    def broken(review_text: str) -> dict:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(sentiment, "predict", broken)
    with pytest.raises(RuntimeError):
        await process(engine)
    assert await game_status(engine) == (AspectStatus.FAILED, "RuntimeError: model exploded")
    assert await unit_count(engine) == 0

    monkeypatch.setattr(sentiment, "predict", real_predict)
    assert await process(engine) == Skipped.FAILED  # no silent retry
    assert await process(engine, force=True) == 4
    assert await game_status(engine) == (AspectStatus.DONE, None)


async def test_aspects_unprocessed_returns_202_then_counts(client, engine, seeded, ml_calls):
    first = await client.get(f"/games/{APPID}/aspects")
    assert first.status_code == 202
    assert first.json()["status"] == "processing"
    assert "aspects" not in first.json()

    # The background task ran after the 202 was sent; the next call reads stored results.
    second = await client.get(f"/games/{APPID}/aspects")
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "ready"
    assert body["overall"] == {"positive": 2, "negative": 2, "total": 4, "positive_pct": 50.0}
    by_aspect = {a["aspect"]: a for a in body["aspects"]}
    assert set(by_aspect) == {"performance", "price", "bugs", "story", "gameplay"}
    # Review 1 has two bugs units but counts once.
    assert (by_aspect["bugs"]["positive"], by_aspect["bugs"]["negative"]) == (0, 1)
    assert (by_aspect["price"]["positive"], by_aspect["price"]["negative"]) == (1, 1)
    assert by_aspect["story"] == {
        "aspect": "story", "positive": 0, "negative": 0, "total": 0, "positive_pct": None,
        "quotes": [],
    }
    assert body["steam_sample"] == {"positive": 4, "negative": 0, "total": 4, "positive_pct": 100.0}
    assert body["disagreements"] == 2  # reviews 1 and 4: predicted negative, thumbs up
    assert body["steam_rating"] == {
        "score_desc": "Very Positive", "positive": 900, "total": 1000, "positive_pct": 90.0
    }

    async with engine.connect() as conn:
        direct = await conn.scalar(
            select(func.count(ReviewAspect.review_id.distinct()))
            .join(Review, Review.id == ReviewAspect.review_id)
            .where(Review.appid == APPID, ReviewAspect.aspect_label == "price",
                   Review.predicted_sentiment == "negative")
        )
    assert by_aspect["price"]["negative"] == direct

    assert len(ml_calls) == 4  # processed once, not per request
    await client.get(f"/games/{APPID}/aspects")
    assert len(ml_calls) == 4


async def test_failed_forced_rerun_keeps_last_success_time(engine, seeded, ml_calls, monkeypatch):
    await process(engine)
    finished = await processed_at(engine)

    def broken(review_text: str) -> dict:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(sentiment, "predict", broken)
    with pytest.raises(RuntimeError):
        await process(engine, force=True)
    assert (await game_status(engine))[0] == AspectStatus.FAILED
    assert await processed_at(engine) == finished


async def test_reviews_filter_by_aspect_and_sentiment(client, engine, seeded, ml_calls):
    await process(engine)

    resp = await client.get(f"/games/{APPID}/reviews", params={"aspect": "bugs", "sentiment": "negative"})
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["items"]] == [1]

    price = (await client.get(f"/games/{APPID}/reviews", params={"aspect": "price"})).json()
    assert [r["id"] for r in price["items"]] == [2, 1]

    empty = await client.get(f"/games/{APPID}/reviews", params={"aspect": "bugs", "sentiment": "positive"})
    assert empty.status_code == 200
    assert empty.json()["total"] == 0 and empty.json()["items"] == []


async def test_reviews_filter_by_disagreement(client, engine, seeded, ml_calls):
    url = f"/games/{APPID}/reviews"

    await process(engine)

    body = (await client.get(url, params={"disagree": True})).json()
    assert sorted(r["id"] for r in body["items"]) == [1, 4]  # the /aspects `disagreements`
    assert body["total"] == (await client.get(f"/games/{APPID}/aspects")).json()["disagreements"]

    both = (await client.get(url, params={"disagree": True, "aspect": "bugs"})).json()
    assert [r["id"] for r in both["items"]] == [1]


async def test_reviews_carry_their_sentiment_and_aspects(client, engine, seeded, ml_calls):
    url = f"/games/{APPID}/reviews"
    before = (await client.get(url)).json()["items"]
    assert {(r["predicted_sentiment"], tuple(r["aspects"])) for r in before} == {(None, ())}

    await process(engine)

    items = {r["id"]: r for r in (await client.get(url)).json()["items"]}
    # Review 1 has two bugs units and a price unit: deduplicated, in ASPECTS order.
    assert (items[1]["predicted_sentiment"], items[1]["aspects"]) == ("negative", ["price", "bugs"])
    assert (items[3]["predicted_sentiment"], items[3]["aspects"]) == ("positive", [])  # all "none"
    assert items[4]["aspects"] == ["performance"]

    # The tagged sentences behind the aspects, in review order.
    assert items[1]["matches"] == [
        {"aspect": "bugs", "text": "It crashes a lot"},
        {"aspect": "bugs", "text": "Another crash today"},
        {"aspect": "price", "text": "Price is fair."},
    ]
    assert items[3]["matches"] == []

    filtered = (await client.get(url, params={"aspect": "bugs", "sentiment": "negative"})).json()
    assert filtered["items"] == [items[1]]

    # A label dropped from ANCHORS since processing is left out, not a 500.
    async with engine.begin() as conn:
        await conn.execute(
            update(ReviewAspect).where(ReviewAspect.aspect_label == "price").values(aspect_label="cost")
        )
    resp = await client.get(url)
    assert resp.status_code == 200
    assert {r["id"]: r["aspects"] for r in resp.json()["items"]}[1] == ["bugs"]


async def test_aspects_trend_has_every_day(client, engine, seeded, ml_calls):
    await add_review(engine, 5, "Love it.")  # 2026-08-01, a month before the seeded ones
    await process(engine)

    body = (await client.get(f"/games/{APPID}/aspects")).json()
    assert body["name"] == "Hades"
    trend = body["trend"]
    assert trend[0] == {"date": "2026-08-01", "positive": 1, "negative": 0}
    assert trend[-4:] == [
        {"date": "2026-09-02", "positive": 0, "negative": 1},
        {"date": "2026-09-03", "positive": 1, "negative": 0},
        {"date": "2026-09-04", "positive": 1, "negative": 0},
        {"date": "2026-09-05", "positive": 0, "negative": 1},
    ]
    assert len(trend) == 36  # Aug 1 .. Sep 5, gap days included as zeros
    assert all(p["positive"] == p["negative"] == 0 for p in trend[1:-4])
    assert sum(p["positive"] + p["negative"] for p in trend) == body["overall"]["total"]


async def test_reviews_filter_on_unprocessed_game_returns_202(client, seeded, ml_calls):
    resp = await client.get(f"/games/{APPID}/reviews", params={"sentiment": "negative"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"

    unfiltered = await client.get(f"/games/{APPID}/reviews")
    assert unfiltered.status_code == 200
    assert unfiltered.json()["total"] == 4


async def test_background_failure_answers_failed_not_processing(
    client, engine, seeded, ml_calls, monkeypatch, caplog
):
    def broken(review_text: str) -> dict:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(sentiment, "predict", broken)
    assert (await client.get(f"/games/{APPID}/aspects")).status_code == 202

    for resp in (
        await client.get(f"/games/{APPID}/aspects"),
        await client.get(f"/games/{APPID}/reviews", params={"aspect": "bugs"}),
    ):
        assert resp.status_code == 500
        assert resp.json()["status"] == "failed"
        assert resp.json()["detail"] == constants.GENERIC_FAILURE  # no exception text
    # The real reason stays server-side: stored on the game and logged.
    assert await game_status(engine) == (AspectStatus.FAILED, "RuntimeError: model exploded")
    assert "model exploded" in caplog.text


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "OperationalError: connection to 10.0.0.5 failed",
        f"{constants.INTERRUPTED_ERROR} (edited)",  # only the exact interrupted reason shows
    ],
)
async def test_failure_detail_hides_every_other_reason(client, engine, seeded, stored):
    async with engine.begin() as conn:
        await conn.execute(
            update(Game).values(aspects_status=AspectStatus.FAILED, aspects_error=stored)
        )
    for resp in (
        await client.get(f"/games/{APPID}/aspects"),
        await client.get(f"/games/{APPID}/reviews", params={"sentiment": "positive"}),
    ):
        assert resp.status_code == 500
        assert resp.json()["detail"] == constants.GENERIC_FAILURE


async def test_game_being_processed_answers_202_without_a_new_run(client, engine, seeded, ml_calls):
    await set_status(engine, AspectStatus.PROCESSING)
    async with engine.connect() as other:  # a live run holds the run lock
        await other.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :appid)"),
            {"ns": constants.PROCESS_LOCK_NAMESPACE, "appid": APPID},
        )
        resp = await client.get(f"/games/{APPID}/aspects")
        assert resp.status_code == 202
        assert ml_calls == []
        assert (await game_status(engine))[0] == AspectStatus.PROCESSING


async def test_run_that_died_while_the_api_is_up_answers_failed(client, engine, seeded, ml_calls):
    # "processing" with nobody holding the run lock: e.g. a Ctrl-C'd CLI run.
    await set_status(engine, AspectStatus.PROCESSING)
    for resp in (
        await client.get(f"/games/{APPID}/aspects"),
        await client.get(f"/games/{APPID}/reviews", params={"aspect": "bugs"}),
    ):
        assert resp.status_code == 500
        assert resp.json()["status"] == "failed"
        assert constants.INTERRUPTED_ERROR in resp.json()["detail"]  # the reason, not just "failed"
        assert f"--force {APPID}" in resp.json()["detail"]
    assert await game_status(engine) == (AspectStatus.FAILED, constants.INTERRUPTED_ERROR)
    assert ml_calls == []


async def test_queued_background_runs_wait_before_touching_the_db(engine, monkeypatch):
    started: list[int] = []
    release = asyncio.Event()

    async def fake_process(session, appid, force=False):
        started.append(appid)
        await release.wait()

    monkeypatch.setattr(service, "process_reviews", fake_process)
    monkeypatch.setattr(service, "_background_lock", asyncio.Lock())  # bound to this loop
    tasks = [
        asyncio.create_task(service.process_in_background(engine, appid)) for appid in (1, 2, 1)
    ]
    await asyncio.sleep(0.05)
    assert started == [1]  # 2 waits without a connection; the second 1 is dropped
    release.set()
    await asyncio.gather(*tasks)
    assert started == [1, 2]
    assert service._queued == set()


async def test_api_starts_when_the_startup_db_check_fails(monkeypatch):
    from src import main

    async def unreachable(session):
        raise OSError("database is waking up")

    monkeypatch.setattr(main, "fail_interrupted_runs", unreachable)
    async with main.lifespan(main.app):
        pass


async def test_missing_sentiment_model_answers_503(client, engine, seeded, monkeypatch, tmp_path):
    real_load = sentiment.load_model
    monkeypatch.setattr(sentiment, "_artifact", None)
    monkeypatch.setattr(sentiment, "load_model", lambda: real_load(tmp_path / "missing.joblib"))

    resp = await client.get(f"/games/{APPID}/aspects")
    assert resp.status_code == 503
    assert "train_sentiment.py" in resp.json()["detail"]
    assert await game_status(engine) == (AspectStatus.PENDING, None)
    assert (await client.get(f"/games/{APPID}/reviews")).status_code == 200  # needs no model


async def test_review_without_units_still_has_sentiment(client, engine, seeded, ml_calls):
    await add_review(engine, 5, "[img]screenshot.png[/img]")
    await process(engine)

    positive = (await client.get(f"/games/{APPID}/reviews", params={"sentiment": "positive"})).json()
    negative = (await client.get(f"/games/{APPID}/reviews", params={"sentiment": "negative"})).json()
    assert 5 in [r["id"] for r in positive["items"]]
    assert positive["total"] + negative["total"] == 5

    overall = (await client.get(f"/games/{APPID}/aspects")).json()["overall"]
    assert overall["total"] == 5


async def test_reviews_rejects_unknown_filter_values(client, seeded):
    url = f"/games/{APPID}/reviews"
    assert (await client.get(url, params={"aspect": "none"})).status_code == 422
    assert (await client.get(url, params={"sentiment": "meh"})).status_code == 422
    assert (await client.get(url, params={"sort": "random"})).status_code == 422
    assert (await client.get(url, params={"day": "yesterday"})).status_code == 422


async def test_reviews_sort_and_day(client, engine, seeded):
    async with engine.begin() as conn:
        await conn.execute(update(Review).where(Review.id == 2).values(votes_up=9))
        await conn.execute(update(Review).where(Review.id == 3).values(playtime_forever=600))
    url = f"/games/{APPID}/reviews"

    async def order(**params) -> list[int]:
        return [r["id"] for r in (await client.get(url, params=params)).json()["items"]]

    assert await order() == [4, 3, 2, 1]  # newest first by default
    assert await order(sort="helpful") == [2, 4, 3, 1]  # ties stay newest first
    assert await order(sort="playtime") == [3, 4, 2, 1]
    # Review 2 was created 2026-09-03 00:00 UTC, the trend's day for it.
    day = (await client.get(url, params={"day": "2026-09-03"})).json()
    assert (day["total"], [r["id"] for r in day["items"]]) == (1, [2])


async def test_filtered_pagination_boundaries(client, engine, seeded, ml_calls):
    await process(engine)
    url = f"/games/{APPID}/reviews"

    last = (await client.get(url, params={"sentiment": "negative", "limit": 1, "offset": 1})).json()
    assert last["total"] == 2
    assert [r["id"] for r in last["items"]] == [1]

    beyond = await client.get(url, params={"sentiment": "negative", "limit": 1, "offset": 5})
    assert beyond.status_code == 200
    assert beyond.json()["total"] == 2 and beyond.json()["items"] == []


async def test_aspects_unknown_app_matches_reviews_404(client, steam):
    steam.get(constants.APPDETAILS_URL).mock(
        return_value=httpx.Response(200, json={"999": {"success": False}})
    )
    aspects_resp = await client.get("/games/999/aspects")
    reviews_resp = await client.get("/games/999/reviews")
    assert aspects_resp.status_code == reviews_resp.status_code == 404
    assert aspects_resp.json() == reviews_resp.json() == {"detail": "Steam has no app with appid 999"}


async def test_on_demand_off_serves_only_processed_games(
    client, engine, seeded, ml_calls, steam, monkeypatch
):
    from src.config import settings

    monkeypatch.setattr(settings, "allow_on_demand_processing", False)
    async with AsyncSession(engine) as session:
        session.add(Game(appid=620, name="Portal 2", aspects_status=AspectStatus.DONE))
        await session.commit()

    for path in (f"/games/{APPID}/aspects", f"/games/{APPID}/reviews", "/games/999/aspects"):
        res = await client.get(path)
        assert res.status_code == 403, path
        assert res.json()["status"] == "unavailable"
        assert res.json()["available"] == [{"appid": 620, "name": "Portal 2"}]
    assert ml_calls == []  # nothing was queued
    assert not steam.calls  # an unknown appid never reaches Steam
    async with engine.connect() as conn:
        status = await conn.scalar(select(Game.aspects_status).where(Game.appid == APPID))
    assert status == AspectStatus.PENDING

    await set_status(engine, AspectStatus.DONE)  # a pre-processed game is served as usual
    assert (await client.get(f"/games/{APPID}/aspects")).status_code == 200
    # A rerun (processing) or a failed run shows its own screen, not "unavailable".
    await set_status(engine, AspectStatus.PROCESSING)
    async with engine.connect() as other:  # a live run holds the run lock
        await other.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :appid)"),
            {"ns": constants.PROCESS_LOCK_NAMESPACE, "appid": APPID},
        )
        assert (await client.get(f"/games/{APPID}/aspects")).status_code == 202
    await set_status(engine, AspectStatus.FAILED)
    assert (await client.get(f"/games/{APPID}/aspects")).status_code == 500
    assert ml_calls == []


async def test_aspect_quotes_prefer_the_weak_side(client, engine, seeded, ml_calls, monkeypatch):
    await add_review(
        engine, 5,
        "Bad price, way too high for what you get here. Crash after crash after crash, "
        "every single session. Love the price of the soundtrack though",
    )
    await process(engine)
    # Picked once by the run and stored; /aspects only reads them, with no model call.
    monkeypatch.setattr(sentiment, "predict_labels", None)
    body = (await client.get(f"/games/{APPID}/aspects")).json()
    quotes = {a["aspect"]: a["quotes"] for a in body["aspects"]}
    async with engine.connect() as conn:
        assert await conn.scalar(select(Game.aspect_quotes)) == quotes
        card = await conn.scalar(select(Game.card_counts))
    # The homepage card's counts are /aspects' own, stored by the same run.
    assert card["aspects"] == [{k: v for k, v in a.items() if k != "quotes"} for a in body["aspects"]]
    assert card["steam_sample"] == body["steam_sample"]
    # Units under QUOTE_MIN_CHARS ("It crashes a lot", "Price is great") never qualify.
    # The thumb is the reviewer's own (add_review's are all up), not the model's reading.
    assert quotes["bugs"] == [{
        "text": "Crash after crash after crash, every single session", "sentiment": "negative",
        "voted_up": True,
    }]
    # The last sentence is in a negative review but reads positive, so it is no example.
    assert quotes["price"] == [{
        "text": "Bad price, way too high for what you get here", "sentiment": "negative",
        "voted_up": True,
    }]
    assert quotes["story"] == []


def test_pick_quotes_fills_from_the_other_side_and_drops_duplicates():
    def side(*texts: str) -> list[tuple[str, bool]]:
        return [(t, False) for t in texts]

    many = {"negative": side("Bad A", "bad a", "Bad B", "Bad C", "Bad D"), "positive": side("Good")}
    assert [q["text"] for q in service._pick_quotes(many, "negative")] == ["Bad A", "Bad B", "Bad C"]
    two = {"negative": side("Bad A", "Bad B"), "positive": side("Good")}
    assert [q["text"] for q in service._pick_quotes(two, "negative")] == ["Bad A", "Bad B"]
    one = {"negative": [("Bad A", True)], "positive": side("Good A", "Good B", "Good C")}
    assert service._pick_quotes(one, "negative") == [
        {"text": "Bad A", "sentiment": "negative", "voted_up": True},
        {"text": "Good A", "sentiment": "positive", "voted_up": False},
        {"text": "Good B", "sentiment": "positive", "voted_up": False},
    ]


async def steam_rating(engine) -> tuple:
    async with engine.connect() as conn:
        return tuple((await conn.execute(select(
            Game.steam_score_desc, Game.steam_positive, Game.steam_total,
            Game.steam_rating_checked_at.is_not(None),
        ))).one())


async def test_steam_rating_is_fetched_by_the_run_not_by_requests(client, engine, seeded,
                                                                   ml_calls, steam_summary):
    await process(engine)
    assert steam_summary.call_count == 1
    params = steam_summary.calls.last.request.url.params  # what Steam's store page shows
    assert (params["language"], params["purchase_type"]) == ("english", "steam")
    assert await steam_rating(engine) == ("Very Positive", 900, 1000, True)
    for _ in range(2):
        rating = (await client.get(f"/games/{APPID}/aspects")).json()["steam_rating"]
        assert rating == {
            "score_desc": "Very Positive", "positive": 900, "total": 1000, "positive_pct": 90.0
        }
    assert steam_summary.call_count == 1


async def test_steam_rating_failure_is_recorded_not_retried(client, engine, seeded, ml_calls,
                                                            steam_summary):
    steam_summary.mock(return_value=httpx.Response(500))
    await process(engine)
    assert (await game_status(engine))[0] == AspectStatus.DONE  # best effort: the run succeeds
    assert await steam_rating(engine) == (None, None, None, True)  # asked, nothing to store
    for _ in range(2):
        resp = await client.get(f"/games/{APPID}/aspects")
        assert resp.status_code == 200 and resp.json()["steam_rating"] is None
    assert steam_summary.call_count == constants.RETRY_ATTEMPTS  # requests never call Steam

    steam_summary.mock(return_value=httpx.Response(200, json={"success": 1}))  # no summary
    await process(engine, force=True)
    assert await steam_rating(engine) == (None, None, None, True)


async def test_forced_rerun_keeps_the_rating_when_steam_fails(engine, seeded, ml_calls,
                                                              steam_summary):
    await process(engine)
    steam_summary.mock(return_value=httpx.Response(500))
    await process(engine, force=True)
    assert await steam_rating(engine) == ("Very Positive", 900, 1000, True)


async def test_game_processed_before_stored_quotes_is_served_without_them(
    client, engine, seeded, ml_calls, monkeypatch, caplog
):
    await process(engine)
    async with engine.begin() as conn:  # as left by migration 0005 on an already-done game
        await conn.execute(update(Game).values(
            aspect_quotes=None, steam_rating_checked_at=None, steam_score_desc=None,
            steam_positive=None, steam_total=None,
        ))
    monkeypatch.setattr(sentiment, "predict_labels", None)  # no model needed to serve it
    resp = await client.get(f"/games/{APPID}/aspects")
    assert resp.status_code == 200
    assert all(a["quotes"] == [] for a in resp.json()["aspects"])
    assert resp.json()["steam_rating"] is None
    assert f"process_reviews.py --force {APPID}" in caplog.text
