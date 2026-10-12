import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Date, cast, delete, exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from src.config import settings
from src.games import service as games_service
from src.games.constants import AspectStatus
from src.games.exceptions import NotFailed, RetryNotAllowed
from src.games.models import Game
from src.games.schemas import (
    AvailableGame,
    FailedStatus,
    ProcessingStatus,
    UnavailableStatus,
)
from src.ml import aspects, sentiment
from src.ml.anchors import ASPECTS
from src.ml.constants import NEGATIVE, POSITIVE
from src.reviews import constants, ingestion
from src.reviews.exceptions import InsufficientReviews, SteamUnavailable
from src.reviews.models import Review, ReviewAspect
from src.reviews.schemas import AspectMatch, ReviewOut

logger = logging.getLogger(__name__)

# Background runs queue here, one at a time, before `process_reviews` takes any connection:
# the memory bound (docs/DECISIONS.md) was measured for a single run, two at once could pass
# Render's 512 MB, and queued runs can't use up the pool that the active run needs for its
# writes. The CLI runs games one after another. `_queued` keeps polling from queueing the
# same app twice.
_background_lock = asyncio.Lock()
_queued: set[int] = set()


async def ensure_ingested(session: AsyncSession, http: httpx.AsyncClient, appid: int) -> None:
    """Fetch and cache the app's reviews unless that already happened.

    Concurrent first requests for one appid serialize on a transaction-scoped advisory
    lock, so Steam is hit once and the loser finds the game already ingested. Nothing is
    persisted (and the game is not marked ingested) when the min-review guard fails.
    """
    if games_service.is_ingested(await games_service.get_game(session, appid)):
        return

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :appid)"),
        {"ns": constants.INGEST_LOCK_NAMESPACE, "appid": appid},
    )
    if games_service.is_ingested(await games_service.get_game(session, appid)):
        return

    logger.info("Ingesting reviews for appid %d", appid)
    name = await ingestion.fetch_app_name(http, appid)
    reviews = await ingestion.fetch_reviews(http, appid, settings.max_reviews)
    if len(reviews) < settings.min_review_count:
        raise InsufficientReviews(appid, len(reviews), settings.min_review_count)

    await games_service.save_game(session, appid, name, datetime.now(UTC))
    for i in range(0, len(reviews), constants.INSERT_CHUNK_SIZE):
        chunk = [{**r, "appid": appid} for r in reviews[i : i + constants.INSERT_CHUNK_SIZE]]
        await session.execute(insert(Review).values(chunk).on_conflict_do_nothing())
    await session.commit()
    logger.info("Ingested %d reviews for appid %d", len(reviews), appid)


async def list_reviews(
    session: AsyncSession,
    appid: int,
    limit: int,
    offset: int,
    aspect: str | None = None,
    sentiment_label: str | None = None,
    sort: constants.ReviewSort = constants.ReviewSort.NEWEST,
    day: date | None = None,
    disagree: bool = False,
) -> tuple[int, list[ReviewOut]]:
    """A page of the app's reviews in `sort` order, each with its stored aspects.

    `aspect` keeps reviews with at least one unit tagged with it; `sentiment_label` keeps
    reviews predicted that way. Both need the app to be processed (`process_reviews`).
    `day` keeps reviews created on that UTC day, the same days as the /aspects trend.
    `disagree` keeps reviews whose predicted sentiment and thumb differ, the ones /aspects
    counts as `disagreements`; it needs the app to be processed too.
    """
    where = [Review.appid == appid]
    if aspect is not None:
        unit = select(ReviewAspect.id).where(
            ReviewAspect.review_id == Review.id, ReviewAspect.aspect_label == aspect
        )
        where.append(exists(unit))
    if sentiment_label is not None:
        where.append(Review.predicted_sentiment == sentiment_label)
    if disagree:
        where.append(Review.predicted_sentiment.is_not(None))
        where.append((Review.predicted_sentiment == POSITIVE) != Review.voted_up)
    if day is not None:
        start = datetime.combine(day, datetime.min.time(), UTC)
        where += [Review.created_at >= start, Review.created_at < start + timedelta(days=1)]

    newest = (Review.created_at.desc(), Review.id.desc())
    order = {
        constants.ReviewSort.NEWEST: newest,
        constants.ReviewSort.HELPFUL: (Review.votes_up.desc(), *newest),
        constants.ReviewSort.PLAYTIME: (Review.playtime_forever.desc(), *newest),
    }[sort]
    total = await session.scalar(select(func.count()).select_from(Review).where(*where))
    rows = await session.scalars(
        select(Review).where(*where).order_by(*order).limit(limit).offset(offset)
    )
    reviews = list(rows)
    units = await session.execute(
        select(ReviewAspect.review_id, ReviewAspect.aspect_label, ReviewAspect.sentence_text)
        .where(
            ReviewAspect.review_id.in_([r.id for r in reviews]),
            # Not "none", nor a label dropped from ANCHORS since the last --force run.
            ReviewAspect.aspect_label.in_(ASPECTS),
        )
        .order_by(ReviewAspect.id)  # inserted in review order
    )
    by_review: dict[int, list[AspectMatch]] = {}
    for review_id, label, sentence in units:
        by_review.setdefault(review_id, []).append(AspectMatch(aspect=label, text=sentence))
    items = []
    for r in reviews:
        matches = by_review.get(r.id, [])
        labels = sorted({m.aspect for m in matches}, key=ASPECTS.index)
        items.append(
            ReviewOut.model_validate(r).model_copy(update={"aspects": labels, "matches": matches})
        )
    return total or 0, items


def _utc_day(column):
    """A timestamptz column as its UTC calendar day, the unit of the /aspects trend."""
    return cast(func.timezone("UTC", column), Date)


def _analyze(reviews: list[tuple[int, str]]) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """Sentiment for each whole review, and each review's aspect-tagged units."""
    # ponytail: one predict/encode call per review; batching across reviews would be several
    # times faster (known limitation, see DECISIONS.md).
    labels: dict[int, str] = {}
    units: list[dict[str, Any]] = []
    for review_id, review_text in reviews:
        labels[review_id] = sentiment.predict(review_text)["label"]
        units.extend(
            {
                "review_id": review_id,
                "sentence_text": unit["sentence"],
                "aspect_label": unit["aspect"],
                "similarity_score": unit["similarity"],
            }
            for unit in aspects.assign_aspects(review_text)
        )
    return labels, units


def _skip_reason(game: Game | None) -> constants.Skipped:
    if not games_service.is_ingested(game):
        return constants.Skipped.NOT_INGESTED
    return {
        AspectStatus.DONE: constants.Skipped.ALREADY_DONE,
        # Claiming failed, but we hold the run lock: whoever set "processing" is gone.
        AspectStatus.PROCESSING: constants.Skipped.INTERRUPTED,
        AspectStatus.FAILED: constants.Skipped.FAILED,
    }[game.aspects_status]


async def process_reviews(
    session: AsyncSession, appid: int, force: bool = False
) -> int | constants.Skipped:
    """Run sentiment + aspect tagging over the app's cached reviews and store the results.

    Returns the number of reviews processed, or why nothing was done. Only a "pending" game
    is claimed; `force` also claims done, failed and stuck "processing" ones (after tuning
    anchors, or when a worker died mid-run). A game another process is running right now is
    skipped even with `force`.

    The run owns the game through a transaction-scoped advisory lock held on a second
    connection for the whole run, taken before the claim; Postgres drops it if the process
    dies. `fail_interrupted_runs` uses it to tell live runs from dead ones. The work itself
    is three short transactions around the CPU work (11-35 s per game on a 32-core desktop;
    14-19 min in the production image limited to Render's 0.1 CPU): claim (status ->
    processing), read the
    texts, then write every result together with status -> done. A failure sets status ->
    failed with the error and re-raises; callers log it. A failed game stays failed until a
    `force` run or a user's `retry_failed` request, never on its own.
    """
    async with session.bind.connect() as owner:
        # Neon ends sessions idle in a transaction after 5 min; a run on Render takes longer.
        await owner.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
        owned = await owner.scalar(
            text("SELECT pg_try_advisory_xact_lock(:ns, :appid)"),
            {"ns": constants.PROCESS_LOCK_NAMESPACE, "appid": appid},
        )
        if not owned:
            return constants.Skipped.RUNNING
        return await _process_owned(session, appid, force)  # the lock goes with `owner`


async def _process_owned(session: AsyncSession, appid: int, force: bool) -> int | constants.Skipped:
    claimable = list(AspectStatus) if force else [AspectStatus.PENDING]
    claimed = await session.scalar(
        update(Game)
        .where(
            Game.appid == appid,
            Game.last_ingested_at.is_not(None),
            Game.aspects_status.in_(claimable),
        )
        .values(aspects_status=AspectStatus.PROCESSING, aspects_error=None)
        .returning(Game.appid)
    )
    if claimed is None:
        reason = _skip_reason(await games_service.get_game(session, appid))
        await session.rollback()
        return reason
    await session.commit()

    try:
        result = await session.execute(
            select(Review.id, Review.review_text).where(Review.appid == appid)
        )
        reviews = list(result.all())
        await session.commit()  # hand the connection back before Steam and the CPU work
        rating = await _fetch_steam_rating(appid)
        logger.info("Processing %d reviews for appid %d", len(reviews), appid)
        labels, units = await asyncio.to_thread(_analyze, reviews)  # keep the event loop free

        # The run lock is the real guard; this row lock only orders the writes if that lock's
        # connection drops mid-run and a second run starts, so their units can't interleave.
        await session.execute(select(Game.appid).where(Game.appid == appid).with_for_update())
        app_reviews = select(Review.id).where(Review.appid == appid)
        await session.execute(delete(ReviewAspect).where(ReviewAspect.review_id.in_(app_reviews)))
        if labels:
            await session.execute(
                update(Review),
                [{"id": rid, "predicted_sentiment": label} for rid, label in labels.items()],
            )
        for i in range(0, len(units), constants.INSERT_CHUNK_SIZE):
            chunk = units[i : i + constants.INSERT_CHUNK_SIZE]
            await session.execute(insert(ReviewAspect).values(chunk))
        summary = await _summary_counts(session, appid)  # reads the rows written above
        quotes = await _select_quotes(session, appid, summary)
        now = datetime.now(UTC)
        await session.execute(
            update(Game)
            .where(Game.appid == appid)
            .values(
                aspects_status=AspectStatus.DONE,
                aspects_processed_at=now,
                aspect_quotes=quotes,
                card_counts={"aspects": summary["aspects"], "steam_sample": summary["steam_sample"]},
                steam_rating_checked_at=now,
                **rating,
            )
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await session.execute(
            update(Game)
            .where(Game.appid == appid)
            .values(aspects_status=AspectStatus.FAILED, aspects_error=f"{type(exc).__name__}: {exc}")
        )
        await session.commit()
        raise
    logger.info("Stored %d aspect units for appid %d", len(units), appid)
    return len(reviews)


async def _fetch_steam_rating(appid: int) -> dict[str, Any]:
    """Steam's store rating as `games` columns, or {} (keep what is stored); best effort.

    Fetched once per run, with no transaction open, so a slow or failing Steam delays the run
    by at most the client's retries and never fails it. The run still records that it asked
    (`steam_rating_checked_at`), so a game Steam has no rating for isn't asked again until
    the next --force run.
    """
    try:
        async with ingestion.new_client() as http:
            rating = await ingestion.fetch_review_summary(http, appid)
    except SteamUnavailable as exc:
        logger.warning("No Steam rating for appid %d: %s", appid, exc.detail)
        return {}
    if rating is None:
        logger.warning("No Steam rating for appid %d: no query_summary", appid)
        return {}
    return rating


async def fail_interrupted_runs(session: AsyncSession, appid: int | None = None) -> int:
    """Mark games (every one, or just `appid`) left in "processing" by a dead run as failed.

    Returns how many. Run at API startup for every game, and by `unprocessed_response` for
    a game a request finds "processing", so a run that dies while the API stays up (a
    Ctrl-C'd or OOM-killed CLI run) is caught too. A game is failed only if no live process
    holds its run lock (see `process_reviews`), so a CLI `--force` run against the same
    database, or another API instance's run during a deploy, is left alone. Otherwise a
    dead run's game would answer 202 forever. Failed rather than pending, so a run that runs
    the host out of memory is not retried into the same crash on the next request.

    The lock is read from `pg_locks`, not taken: taking it, even briefly, made a run that
    started in that moment skip itself as RUNNING. The read sits in the UPDATE's WHERE, so a
    run that takes the lock and claims the row mid-statement is caught when Postgres
    rechecks the WHERE against the claimed row.
    """
    no_live_run = text(
        "NOT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory'"
        " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        " AND classid = :ns AND objid = games.appid AND objsubid = 2 AND granted)"
    ).bindparams(ns=constants.PROCESS_LOCK_NAMESPACE)
    query = (
        update(Game)
        .where(Game.aspects_status == AspectStatus.PROCESSING, no_live_run)
        .values(aspects_status=AspectStatus.FAILED, aspects_error=constants.INTERRUPTED_ERROR)
    )
    if appid is not None:
        query = query.where(Game.appid == appid)
    result = await session.execute(query)
    await session.commit()
    if result.rowcount:
        logger.warning("Marked %d interrupted analysis run(s) as failed", result.rowcount)
    return result.rowcount


async def process_in_background(engine: AsyncEngine, appid: int) -> None:
    """Runs after the 202 is sent, in its own session: the request's is closed by then.

    A queued game stays "pending" (answered 202 like "processing") and holds no connection
    until its turn; only then does `process_reviews` claim it.
    """
    if appid in _queued:
        return
    _queued.add(appid)
    try:
        async with _background_lock, AsyncSession(engine, expire_on_commit=False) as session:
            await process_reviews(session, appid)
    except Exception:
        # Already recorded as failed on the game (unless the DB itself is what failed).
        logger.exception("Processing reviews for appid %d failed", appid)
    finally:
        _queued.discard(appid)


async def on_demand_blocked(
    session: AsyncSession, appid: int, game: Game | None
) -> JSONResponse | None:
    """With on-demand processing off, the 403 for an app not processed ahead of time; else None.

    `game` is the caller's lookup of `appid`. Checked before ingestion, so an unknown appid
    never reaches Steam or starts a run that the deployed host's CPU can't finish (see
    `settings.allow_on_demand_processing`). Only an uningested or pending game is blocked:
    a processing one (a CLI `--force` rerun) answers 202 and a failed one 500, as usual.
    """
    if settings.allow_on_demand_processing:
        return None
    if games_service.opens_without_on_demand(game):
        return None
    available = await games_service.list_processed(session)
    body = UnavailableStatus(
        appid=appid,
        status="unavailable",
        detail="This deployment only serves games analyzed ahead of time: on its free-tier "
        "host, a new game would take 14-19 minutes to analyze. Run the project locally to "
        "analyze any appid.",
        available=[AvailableGame(appid=g.appid, name=g.name) for g in available],
    )
    return JSONResponse(status_code=403, content=body.model_dump())


def _failure_detail(game: Game) -> str:
    """The 500 detail for a failed game: the reason only for an interrupted run.

    Any other stored reason is a raw exception message (it can quote SQL or file paths), so
    visitors get a generic line; the exception itself is logged where it happened
    (`process_in_background`, the CLI) and kept in `games.aspects_error`.
    """
    if game.aspects_error == constants.INTERRUPTED_ERROR:
        return (
            f"Analyzing reviews for app {game.appid} failed: {constants.INTERRUPTED_ERROR}. "
            "It needs a manual restart by the project owner "
            f"(scripts/process_reviews.py --force {game.appid})."
        )
    return constants.GENERIC_FAILURE


async def unprocessed_response(session: AsyncSession, game: Game) -> JSONResponse:
    """The answer for an app whose results aren't ready (status other than done).

    failed -> 500 with status "failed"; pending -> 202 and processing starts after sending,
    or 503 if no sentiment model is trained yet; processing -> 202, nothing started, unless
    the run behind it is dead (`fail_interrupted_runs`), which makes it failed.
    Returned rather than raised: an exception handler's response would drop the task.
    """
    appid = game.appid
    if game.aspects_status == AspectStatus.PROCESSING and await fail_interrupted_runs(
        session, appid
    ):
        await session.refresh(game)
    if game.aspects_status == AspectStatus.FAILED:
        body = FailedStatus(
            appid=appid,
            status="failed",
            detail=_failure_detail(game),
            retryable=settings.allow_on_demand_processing,
            out_of_memory=(game.aspects_error or "").startswith(constants.MEMORY_ERROR_PREFIX),
        )
        return JSONResponse(status_code=500, content=body.model_dump())
    task = None
    if game.aspects_status == AspectStatus.PENDING:
        sentiment.ensure_loaded()  # 503 now beats a run that is bound to fail
        task = BackgroundTask(process_in_background, session.bind, appid)
    body = ProcessingStatus(
        appid=appid,
        status="processing",
        detail=f"Reviews for app {appid} are being analyzed; retry shortly",
    )
    return JSONResponse(status_code=202, content=body.model_dump(), background=task)


async def retry_failed(session: AsyncSession, game: Game) -> JSONResponse:
    """A user's request to rerun a failed analysis (the dashboard's "Try again" button).

    Moves failed -> pending, then answers like a GET on a pending game (202, run queued).
    Failed games are never retried by a GET, so a run that kills the host isn't repeated on
    every poll; this runs only on an explicit POST, one run per click. Refused where
    on-demand processing is off (the deployed demo): there a visitor could otherwise start
    14-19 min runs on the host's CPU. A game that is already pending or processing just
    answers its 202, so a double click starts nothing twice.
    """
    if not settings.allow_on_demand_processing:
        raise RetryNotAllowed
    if game.aspects_status == AspectStatus.DONE:
        raise NotFailed(game.appid)
    await session.execute(
        update(Game)
        .where(Game.appid == game.appid, Game.aspects_status == AspectStatus.FAILED)
        .values(aspects_status=AspectStatus.PENDING, aspects_error=None)
    )
    await session.commit()
    await session.refresh(game)
    return await unprocessed_response(session, game)


async def aspect_summary(session: AsyncSession, game: Game) -> dict[str, Any]:
    """Positive/negative counts per aspect and per day for a processed app, with its quotes.

    A pure read: the quotes were picked by the run that processed the app
    (`games.aspect_quotes`). No model is loaded here.
    """
    summary = await _summary_counts(session, game.appid)
    if game.aspect_quotes is None:
        logger.warning(
            "appid %d was processed before quotes and Steam's rating were stored; serving "
            "it without them until scripts/process_reviews.py --force %d",
            game.appid, game.appid,
        )
    for entry in summary["aspects"]:
        entry["quotes"] = (game.aspect_quotes or {}).get(entry["aspect"], [])
    return {"appid": game.appid, "status": "ready", **summary}


async def _select_quotes(
    session: AsyncSession, appid: int, summary: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Each aspect's example sentences (`_pick_quotes`), from the app's stored results.

    Run once by `process_reviews` inside its final write transaction, so it sees that run's
    rows; the result is stored in `games.aspect_quotes`. A weak aspect (under 50% positive,
    or well below the game's overall share) leans negative, any other positive.
    """
    candidates = await _quote_candidates(session, appid)
    overall_pct = summary["overall"]["positive_pct"] or 0
    quotes = {}
    for entry in summary["aspects"]:
        pct = entry["positive_pct"]
        weak = pct is not None and (
            pct < 50 or pct < overall_pct - constants.QUOTE_WEAK_MARGIN_PCT
        )
        quotes[entry["aspect"]] = _pick_quotes(
            candidates.get(entry["aspect"], {}), NEGATIVE if weak else POSITIVE
        )
    return quotes


async def _summary_counts(session: AsyncSession, appid: int) -> dict[str, Any]:
    """The counts behind /aspects, quotes aside: `overall`, `aspects`, `trend` and the rest.

    Counts are distinct reviews, not units: sentiment is predicted per review, so a review
    with three bugs sentences counts once under bugs. `overall` counts every review, also
    those whose units are all "none" or that have no units at all. `trend` has one point
    per UTC day from the oldest to the newest review, days without reviews included as 0.
    `steam_sample` counts Steam's own thumbs (voted_up) on the same reviews as `overall`,
    and `disagreements` those where the model's call and the reviewer's thumb differ.
    """
    # ponytail: two queries; one GROUP BY GROUPING SETS would do (known limitation, see
    # DECISIONS.md).
    rows = await session.execute(
        select(
            ReviewAspect.aspect_label,
            Review.predicted_sentiment,
            func.count(ReviewAspect.review_id.distinct()),
        )
        .join(Review, Review.id == ReviewAspect.review_id)
        .where(Review.appid == appid)
        .group_by(ReviewAspect.aspect_label, Review.predicted_sentiment)
    )
    counts = {(label, sent): n for label, sent, n in rows}
    day = _utc_day(Review.created_at)
    daily = await session.execute(
        select(day, Review.predicted_sentiment, Review.voted_up, func.count())
        .where(Review.appid == appid)
        .group_by(day, Review.predicted_sentiment, Review.voted_up)
    )
    by_day: dict[date, dict[str, int]] = {}
    overall_counts: dict[str, int] = {}
    voted_up = {True: 0, False: 0}
    disagreements = 0
    for d, sent, up, n in daily:
        day_counts = by_day.setdefault(d, {})
        day_counts[sent] = day_counts.get(sent, 0) + n
        overall_counts[sent] = overall_counts.get(sent, 0) + n
        if sent is not None:  # only the reviews `overall` counts
            voted_up[up] += n
            if (sent == POSITIVE) != up:
                disagreements += n
    trend = []
    if by_day:
        d, last = min(by_day), max(by_day)
        while d <= last:
            trend.append({
                "date": d,
                "positive": by_day.get(d, {}).get(POSITIVE, 0),
                "negative": by_day.get(d, {}).get(NEGATIVE, 0),
            })
            d += timedelta(days=1)

    entry = games_service.sentiment_counts
    return {
        "overall": entry(overall_counts.get(POSITIVE, 0), overall_counts.get(NEGATIVE, 0)),
        "aspects": [  # "none" is not one of ASPECTS
            {"aspect": a, **entry(counts.get((a, POSITIVE), 0), counts.get((a, NEGATIVE), 0))}
            for a in ASPECTS
        ],
        "trend": trend,
        "steam_sample": entry(voted_up[True], voted_up[False]),
        "disagreements": disagreements,
    }


async def _quote_candidates(
    session: AsyncSession, appid: int
) -> dict[str, dict[str, list[tuple[str, bool]]]]:
    """aspect -> sentiment -> the most aspect-like sentences of that game, best first, each
    with its reviewer's thumb (`voted_up`).

    Ranked by similarity to the aspect's anchors, the same score that tagged them, within
    QUOTE_MIN_CHARS..QUOTE_MAX_CHARS. A sentence is kept only if the sentiment model, run on
    the sentence alone, agrees with its review's predicted sentiment: otherwise a positive
    remark inside a negative review ("great performance, no lag") would be shown as a
    complaint. The model is trained on whole reviews and is weak on single sentences, so
    agreement filters out more good quotes than it needs to; it is a precision filter.
    """
    rank = func.row_number().over(
        partition_by=(ReviewAspect.aspect_label, Review.predicted_sentiment),
        order_by=(ReviewAspect.similarity_score.desc(), ReviewAspect.id),
    )
    ranked = (
        select(
            ReviewAspect.aspect_label,
            Review.predicted_sentiment,
            ReviewAspect.sentence_text,
            Review.voted_up,
            rank.label("rank"),
        )
        .join(Review, Review.id == ReviewAspect.review_id)
        .where(
            Review.appid == appid,
            ReviewAspect.aspect_label.in_(ASPECTS),
            func.length(ReviewAspect.sentence_text).between(
                constants.QUOTE_MIN_CHARS, constants.QUOTE_MAX_CHARS
            ),
        )
        .subquery()
    )
    rows = await session.execute(
        select(
            ranked.c.aspect_label,
            ranked.c.predicted_sentiment,
            ranked.c.sentence_text,
            ranked.c.voted_up,
        )
        .where(ranked.c.rank <= constants.QUOTE_CANDIDATES)
        .order_by(ranked.c.rank)
    )
    rows = list(rows)
    agrees = await asyncio.to_thread(sentiment.predict_labels, [s for _, _, s, _ in rows])
    out: dict[str, dict[str, list[tuple[str, bool]]]] = {}
    for (aspect, sent, sentence, up), own in zip(rows, agrees, strict=True):
        if own == sent:
            out.setdefault(aspect, {}).setdefault(sent, []).append((sentence, up))
    return out


def _pick_quotes(
    candidates: dict[str, list[tuple[str, bool]]], preferred: str
) -> list[dict[str, Any]]:
    """Up to QUOTES_PER_ASPECT distinct sentences, from the `preferred` sentiment first.

    The other sentiment fills in only when the preferred one has fewer than
    QUOTE_MIN_FROM_PREFERRED, so a weak aspect's examples show what people complain about.
    """
    other = POSITIVE if preferred == NEGATIVE else NEGATIVE
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sent in (preferred, other):
        if sent == other and len(picked) >= constants.QUOTE_MIN_FROM_PREFERRED:
            break
        for sentence, up in candidates.get(sent, []):
            if len(picked) == constants.QUOTES_PER_ASPECT:
                break
            key = sentence.casefold()
            if key not in seen:
                seen.add(key)
                picked.append({"text": sentence, "sentiment": sent, "voted_up": up})
    return picked
