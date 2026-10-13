"""Compare the M2 and shipped (M10) sentiment models on games neither was trained on.

The M10 follow-up to `portal_flips.py`: whether the new model's habit of reading short ironic
praise as negative shows up beyond Portal 2's test set (see docs/DECISIONS.md, 2026-10-13).
Prints accuracy, negative F1 and precision for both models per game, and how many thumbs-up
reviews the new model newly misreads vs. newly gets right.

Run from `backend/` against the app database (the default appids, Portal, Counter-Strike,
Cyberpunk 2077 and AION 2, are cached in the local one; pass others to check them):
`python scripts/unseen_check.py [APPID ...]`
"""

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import joblib

from scripts.compare_sentiment import ORIGINAL_APPIDS, PREVIOUS_MODEL_PATH
from scripts.train_sentiment import drop_empty, load_reviews, per_class_metrics

DEFAULT_APPIDS = [400, 10, 1091500, 3393110]


def main(appids: list[int]) -> int:
    from src.ml.constants import SENTIMENT_MODEL_PATH

    if seen := ORIGINAL_APPIDS.intersection(appids):
        sys.exit(f"{sorted(seen)} were in the M2 training data; pick unseen games")
    old = joblib.load(PREVIOUS_MODEL_PATH)["pipeline"]
    new = joblib.load(SENTIMENT_MODEL_PATH)["pipeline"]
    reviews, _ = drop_empty(asyncio.run(load_reviews()))
    for appid in appids:
        rows = [r for r in reviews if r.appid == appid]
        if not rows:
            print(f"{appid}: no cached reviews in this database")
            continue
        texts, y = [r.text for r in rows], [r.voted_up for r in rows]
        a = [bool(p) for p in old.predict(texts)]
        b = [bool(p) for p in new.predict(texts)]
        ma, mb = per_class_metrics(y, a), per_class_metrics(y, b)
        newly_wrong = sum(1 for t, x, z in zip(y, a, b, strict=True) if t and x and not z)
        newly_right = sum(1 for t, x, z in zip(y, a, b, strict=True) if t and not x and z)
        neg_a, neg_b = ma["classes"]["negative"], mb["classes"]["negative"]
        print(f"{rows[0].game[:20]:<20} n={len(rows)} pos={sum(y) / len(y):.1%} | accuracy "
              f"{ma['accuracy']:.3f} -> {mb['accuracy']:.3f} | neg F1 {neg_a['f1']:.3f} -> "
              f"{neg_b['f1']:.3f} | neg precision {neg_a['precision']:.3f} -> "
              f"{neg_b['precision']:.3f} | thumbs-up newly misread {newly_wrong}, "
              f"newly right {newly_right}")
    return 0


if __name__ == "__main__":
    sys.exit(main([int(a) for a in sys.argv[1:]] or DEFAULT_APPIDS))
