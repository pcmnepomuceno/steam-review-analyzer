from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.games.constants import AspectStatus

_STATUSES = ", ".join(f"'{s}'" for s in AspectStatus)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint(f"aspects_status IN ({_STATUSES})", name="ck_games_aspects_status"),
    )

    appid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by reviews.service.process_reviews. "done" means review_aspects and
    # reviews.predicted_sentiment hold results for every cached review; ingestion never
    # refreshes a game, so it stays done.
    aspects_status: Mapped[str] = mapped_column(
        String(16), server_default=AspectStatus.PENDING.value
    )
    aspects_error: Mapped[str | None] = mapped_column(Text)  # last failure, when "failed"
    # When a run last finished successfully; kept through later failed or forced runs, so it
    # says how old the stored results are (e.g. for a future refresh policy).
    aspects_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Example sentences per aspect for /aspects ({aspect: [{"text", "sentiment", "voted_up"}]}), picked
    # once by the run that sets "done". NULL on a game processed before this column existed:
    # it is served without quotes until a --force rerun.
    aspect_quotes: Mapped[dict[str, list[dict[str, Any]]] | None] = mapped_column(JSONB)
    # Steam's own store rating (English, Steam purchasers, all time), fetched by each run
    # (best effort; a failed fetch keeps the previous values). `steam_rating_checked_at` is
    # when a run last asked: set with steam_total NULL means Steam had no rating to give or
    # the fetch failed; NULL means the game was processed before runs fetched it.
    steam_score_desc: Mapped[str | None] = mapped_column(Text)  # e.g. "Very Positive"
    steam_positive: Mapped[int | None] = mapped_column(Integer)
    steam_total: Mapped[int | None] = mapped_column(Integer)
    steam_rating_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The homepage card's inputs, stored by the run that sets "done" so `GET /games` doesn't
    # re-aggregate review_aspects per visit: {"aspects": [AspectCounts...], "steam_sample":
    # SentimentCounts}, the same counts /aspects gives. NULL only on a game not yet processed.
    card_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
