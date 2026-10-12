"""Fetch and cache one game's reviews, as the API does on a first request (no API needed).

Used by `setup.sh` to seed the database. Already-ingested games are left alone. A game Steam
doesn't know, one with too few English reviews or a Steam outage prints the reason, persists
nothing and exits 1.

Run from `backend/`: `python scripts/ingest_game.py APPID`
"""

import asyncio
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def run(appid: int) -> int:
    from src.database import SessionLocal, engine
    from src.exceptions import AppError
    from src.reviews.ingestion import new_client
    from src.reviews.service import ensure_ingested

    try:
        async with SessionLocal() as session:
            start = time.perf_counter()
            async with new_client() as http:
                await ensure_ingested(session, http, appid)
            print(f"{appid}: cached ({time.perf_counter() - start:.1f}s)")
        return 0
    except AppError as exc:
        print(f"{appid}: cannot ingest: {exc.detail}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("usage: python scripts/ingest_game.py APPID")
    sys.exit(asyncio.run(run(int(sys.argv[1]))))
