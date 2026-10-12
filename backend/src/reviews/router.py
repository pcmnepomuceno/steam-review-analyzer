from datetime import date
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_session
from src.games import service as games_service
from src.games.constants import AspectStatus
from src.games.dependencies import valid_appid
from src.games.schemas import UNPROCESSED_RESPONSES
from src.ml.anchors import ASPECTS
from src.ml.constants import NEGATIVE, POSITIVE
from src.reviews import service
from src.reviews.constants import ReviewSort
from src.reviews.dependencies import get_http_client
from src.reviews.schemas import ReviewPage

router = APIRouter(prefix="/games/{appid}/reviews", tags=["reviews"])


@router.get("", response_model=ReviewPage, responses=UNPROCESSED_RESPONSES)
async def get_reviews(
    appid: Annotated[int, Depends(valid_appid)],
    # Closed when this returns, before any background processing run (see games/router.py).
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    aspect: Annotated[Literal[ASPECTS] | None, Query()] = None,
    sentiment: Annotated[Literal[POSITIVE, NEGATIVE] | None, Query()] = None,
    sort: ReviewSort = ReviewSort.NEWEST,
    day: date | None = None,  # a UTC day, as in the /aspects trend
    disagree: bool = False,  # only reviews where the model's call and the thumb differ
) -> ReviewPage | JSONResponse:
    game = await games_service.get_game(session, appid)
    if (blocked := await service.on_demand_blocked(session, appid, game)) is not None:
        return blocked
    if not games_service.is_ingested(game):
        await service.ensure_ingested(session, http, appid)
        game = await games_service.get_game(session, appid)
    processed_only = aspect is not None or sentiment is not None or disagree
    if processed_only and game.aspects_status != AspectStatus.DONE:
        return await service.unprocessed_response(session, game)
    total, items = await service.list_reviews(
        session, appid, limit, offset, aspect, sentiment, sort, day, disagree
    )
    return ReviewPage(appid=appid, total=total, limit=limit, offset=offset, items=items)
