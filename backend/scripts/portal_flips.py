"""List the Portal 2 test reviews whose predicted sentiment differs between the M2 model and
the shipped (M10) model, with each model's strongest negative-pushing terms for that review.

Written to check whether Portal 2's negative-F1 drop in the M10 comparison (0.429 -> 0.300 on
4 thumbs-down reviews) was a handful of borderline reviews or a new systematic weakness (see
docs/DECISIONS.md, 2026-10-13). Uses the same test rows as `compare_sentiment.py`.

Run from `backend/` against the local training database:
`DATABASE_URL=postgresql+asyncpg://steam:steam@localhost:5433/steam_reviews_train \\
    python scripts/portal_flips.py [APPID]`  (default 620, Portal 2; any original M2 game works)
"""

import asyncio
import sys
import textwrap
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import joblib
import numpy as np

from scripts.compare_sentiment import ORIGINAL_APPIDS, PREVIOUS_MODEL_PATH, split
from scripts.train_sentiment import drop_empty, load_reviews


def top_terms(pipeline, text: str, k: int = 6) -> list[tuple[str, float]]:
    """The k terms that push this text hardest toward negative (tf-idf weight x coefficient)."""
    tfidf, clf = pipeline.named_steps["tfidf"], pipeline.named_steps["clf"]
    x = tfidf.transform([text])
    contrib = x.data * clf.coef_[0][x.indices]
    names = tfidf.get_feature_names_out()
    return [(str(names[x.indices[i]]), round(float(contrib[i]), 2))
            for i in np.argsort(contrib)[:k]]


def main(appid: int) -> int:
    from src.ml.constants import SENTIMENT_MODEL_PATH

    if appid not in ORIGINAL_APPIDS:
        sys.exit(f"{appid} is not one of the original M2 games {sorted(ORIGINAL_APPIDS)}")
    old = joblib.load(PREVIOUS_MODEL_PATH)["pipeline"]
    new = joblib.load(SENTIMENT_MODEL_PATH)["pipeline"]
    reviews, _ = drop_empty(asyncio.run(load_reviews()))
    _, test = split([r for r in reviews if r.appid in ORIGINAL_APPIDS])
    rows = [r for r in test if r.appid == appid]
    texts = [r.text for r in rows]
    y = np.array([r.voted_up for r in rows])
    p_old, p_new = old.predict_proba(texts)[:, 1], new.predict_proba(texts)[:, 1]  # P(positive)

    print(f"{rows[0].game} test rows: {len(rows)}, thumbs-down: {int((~y).sum())}")
    for name, p in (("M2 ", p_old), ("new", p_new)):
        pred = p >= 0.5
        print(f"  {name}: thumbs-down caught {int((~y & ~pred).sum())}, missed "
              f"{int((~y & pred).sum())}; thumbs-up read as negative {int((y & ~pred).sum())}")

    flips = [i for i in range(len(rows)) if (p_old[i] >= 0.5) != (p_new[i] >= 0.5)]
    print(f"\nFlipped: {len(flips)}")
    for i in flips:
        status = "now WRONG" if (p_new[i] >= 0.5) != y[i] else "now RIGHT"
        print(f"\n--- thumb {'UP' if y[i] else 'DOWN'} | P(positive) M2 {p_old[i]:.3f} -> "
              f"new {p_new[i]:.3f} | {status}")
        print(textwrap.indent(textwrap.fill(texts[i][:600], 100), "    "))
        print("    new model's strongest negative-pushing terms:", top_terms(new, texts[i]))
        print("    M2 model's for the same text:                ", top_terms(old, texts[i]))
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 620))
