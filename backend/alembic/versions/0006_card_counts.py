"""stored homepage card counts

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 12:00:00

`GET /games` stops re-aggregating `review_aspects` on every visit: process_reviews stores
each game's per-aspect counts and Steam's thumbs on the sample in `games.card_counts`. Done
games are backfilled here from the rows they already have (same counts as `_summary_counts`),
so no --force rerun is needed.
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: str | Sequence[str] | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the aspects of the day: a migration must not follow later changes to ANCHORS.
ASPECTS = ("performance", "price", "bugs", "story", "gameplay")


def _counts(positive: int, negative: int) -> dict:
    total = positive + negative
    return {
        "positive": positive,
        "negative": negative,
        "total": total,
        "positive_pct": round(100 * positive / total, 1) if total else None,
    }


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('games', sa.Column('card_counts', postgresql.JSONB(), nullable=True))
    conn = op.get_bind()
    for (appid,) in conn.execute(sa.text("SELECT appid FROM games WHERE aspects_status = 'done'")):
        aspects = {
            (label, sent): n
            for label, sent, n in conn.execute(
                sa.text(
                    "SELECT ra.aspect_label, r.predicted_sentiment, count(DISTINCT ra.review_id) "
                    "FROM review_aspects ra JOIN reviews r ON r.id = ra.review_id "
                    "WHERE r.appid = :appid GROUP BY 1, 2"
                ),
                {"appid": appid},
            )
        }
        votes = {
            up: n
            for up, n in conn.execute(
                sa.text(
                    "SELECT voted_up, count(*) FROM reviews "
                    "WHERE appid = :appid AND predicted_sentiment IS NOT NULL GROUP BY 1"
                ),
                {"appid": appid},
            )
        }
        card = {
            "aspects": [
                {"aspect": a, **_counts(aspects.get((a, "positive"), 0), aspects.get((a, "negative"), 0))}
                for a in ASPECTS
            ],
            "steam_sample": _counts(votes.get(True, 0), votes.get(False, 0)),
        }
        conn.execute(
            sa.text("UPDATE games SET card_counts = CAST(:card AS jsonb) WHERE appid = :appid"),
            {"card": json.dumps(card), "appid": appid},
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('games', 'card_counts')
