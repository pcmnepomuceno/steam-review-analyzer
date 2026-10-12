from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.games import service
from src.games.constants import AspectStatus
from src.games.dependencies import valid_appid
from src.games.models import Game
from src.games.schemas import (
    UNPROCESSED_RESPONSES,
    AspectSummary,
    GameCard,
    SearchResult,
    SteamRating,
)
from src.reviews import service as reviews_service
from src.reviews.dependencies import get_http_client

router = APIRouter(prefix="/games", tags=["games"])
steam_router = APIRouter(prefix="/steam", tags=["steam"])


@router.get("", response_model=list[GameCard])
async def list_games(session: Annotated[AsyncSession, Depends(get_session)]) -> list[GameCard]:
    """Every game whose results are ready, by name, with the counts its homepage card states."""
    return await service.list_cards(session)


@router.get("/{appid}/aspects", response_model=AspectSummary, responses=UNPROCESSED_RESPONSES)
async def get_aspects(
    appid: Annotated[int, Depends(valid_appid)],
    # scope="function" closes the session when this returns, before the response and any
    # background processing run, so the request's connection isn't held for that run.
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AspectSummary | JSONResponse:
    game = await service.get_game(session, appid)
    if (blocked := await reviews_service.on_demand_blocked(session, appid, game)) is not None:
        return blocked
    if not service.is_ingested(game):
        await reviews_service.ensure_ingested(session, http, appid)
        game = await service.get_game(session, appid)
    if game.aspects_status != AspectStatus.DONE:
        return await reviews_service.unprocessed_response(session, game)
    summary = await reviews_service.aspect_summary(session, game)
    rating = None
    if game.steam_total is not None:
        rating = SteamRating(
            score_desc=game.steam_score_desc,
            positive=game.steam_positive,
            total=game.steam_total,
            positive_pct=(
                round(100 * game.steam_positive / game.steam_total, 1) if game.steam_total else None
            ),
        )
    return AspectSummary(name=game.name, steam_rating=rating, **summary)


@steam_router.get("/search", response_model=list[SearchResult])
async def search(
    q: Annotated[str, Query(min_length=2, max_length=100)],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SearchResult]:
    """Steam store search, proxied because browsers can't call it (no CORS headers).

    Not limited to analyzed games: each result says what opening it here gives, so the
    typeahead can tell a game it can't open from one it can.
    """
    results = await service.search_steam(http, q)
    games = await service.get_games(session, [r["appid"] for r in results])
    return [SearchResult(**r, availability=_availability(games.get(r["appid"]))) for r in results]


def _availability(game: Game | None) -> str:
    """Mirrors what `/aspects` answers for the game (see `on_demand_blocked`)."""
    if game is not None and game.aspects_status == AspectStatus.DONE:
        return "analyzed"
    if settings.allow_on_demand_processing or service.opens_without_on_demand(game):
        return "on_demand"
    return "unavailable"
