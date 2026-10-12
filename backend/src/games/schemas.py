from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class SentimentCounts(BaseModel):
    """Distinct reviews by predicted sentiment."""

    positive: int
    negative: int
    total: int
    positive_pct: float | None  # None when total is 0


class Quote(BaseModel):
    """One aspect-tagged sentence, with the predicted sentiment of the review it came from."""

    text: str
    sentiment: Literal["positive", "negative"]
    # That reviewer's thumb on Steam; None for quotes stored before it was (until --force).
    voted_up: bool | None = None


class AspectCounts(SentimentCounts):
    aspect: str


class AspectSentiment(AspectCounts):
    # Up to 3 example sentences: mostly negative for a weak aspect (under 50% positive, or
    # well below the game's overall share), mostly positive otherwise; may be empty.
    quotes: list[Quote]


class SteamRating(BaseModel):
    """Steam's own store rating, as its store page shows it: English reviews, all time."""

    score_desc: str  # Steam's label, e.g. "Very Positive"
    positive: int
    total: int
    positive_pct: float | None  # None when total is 0


class TrendPoint(BaseModel):
    """Reviews created on one UTC day, by predicted sentiment."""

    date: date
    positive: int
    negative: int


class AspectSummary(BaseModel):
    appid: int
    name: str
    status: Literal["ready"]
    overall: SentimentCounts  # every processed review, including those with no aspect
    aspects: list[AspectSentiment]
    trend: list[TrendPoint]  # oldest day first, every day between oldest and newest review
    # Steam's thumbs (voted_up) on the same reviews as `overall`, for a like-for-like check.
    steam_sample: SentimentCounts
    # Reviews in `overall` whose predicted sentiment differs from the reviewer's thumb.
    disagreements: int
    steam_rating: SteamRating | None  # None if Steam had none when the game was processed


class ProcessingStatus(BaseModel):
    """202 while the app's reviews are being analyzed."""

    appid: int
    status: Literal["processing"]
    detail: str


class FailedStatus(BaseModel):
    """500 once the analysis has failed; it stays failed until a --force rerun."""

    appid: int
    status: Literal["failed"]
    detail: str  # the reason and retry command for an interrupted run, else a generic line


class AvailableGame(BaseModel):
    appid: int
    name: str


class GameListItem(AvailableGame):
    review_count: int
    analyzed_at: datetime | None  # games.aspects_processed_at


class GameCard(GameListItem):
    """A homepage card: enough of /aspects to state the game's headline comparison."""

    aspects: list[AspectCounts]
    steam_sample: SentimentCounts


class SearchResult(BaseModel):
    appid: int
    name: str
    image: str | None  # Steam's small capsule image URL
    # What opening it here gives: results ready ("analyzed"), an analysis run first
    # ("on_demand"), or the 403 of a deployment without on-demand processing ("unavailable").
    availability: Literal["analyzed", "on_demand", "unavailable"]


class UnavailableStatus(BaseModel):
    """403 when on-demand processing is off and the app wasn't processed ahead of time."""

    appid: int
    status: Literal["unavailable"]
    detail: str
    available: list[AvailableGame]


# OpenAPI docs for the non-200 answers of endpoints that need processed results.
UNPROCESSED_RESPONSES = {
    202: {"model": ProcessingStatus, "description": "Analysis running; retry shortly"},
    403: {
        "model": UnavailableStatus,
        "description": "On-demand processing is off and this app isn't pre-processed",
    },
    500: {"model": FailedStatus, "description": "Analysis failed; needs a --force rerun"},
    503: {"description": "No sentiment model trained yet"},
}
