from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AspectMatch(BaseModel):
    """One sentence of a review and the aspect it was tagged with."""

    aspect: str
    text: str


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_text: str
    voted_up: bool
    votes_up: int
    playtime_forever: int
    language: str
    created_at: datetime
    predicted_sentiment: str | None  # None until the game is processed
    aspects: list[str] = []  # distinct aspects of the review's units, "none" left out
    matches: list[AspectMatch] = []  # the units behind `aspects`, in review order


class ReviewPage(BaseModel):
    appid: int
    total: int
    limit: int
    offset: int
    items: list[ReviewOut]
