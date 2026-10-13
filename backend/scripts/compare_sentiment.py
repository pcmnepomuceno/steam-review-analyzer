"""Train the sentiment model on the expanded M10 corpus and compare it with the M2 model.

M10. Same methodology as `train_sentiment.py` (TF-IDF + logistic regression, balanced class
weights, stratified 80/20, seed 42), on 33 games instead of 5. The M2 model it is compared
with is kept as `models_store/sentiment_m2.joblib`.

Without flags nothing the app uses is written: the new model goes to
`models_store/sentiment_candidate.joblib` (gitignored) and the report to
`docs/eval/sentiment_candidate_report.txt`. `--adopt` writes them to
`models_store/sentiment.joblib` and `docs/eval/sentiment_report.txt` instead. That is how the
shipped model was produced (adopted 2026-10-13, see docs/DECISIONS.md); training is
deterministic, so rerunning it on the same corpus rebuilds the same model.

The split keeps the M2 model's test set intact. The original games are split exactly as
`train_sentiment.py` split them (same rows, same order, same seed), and the M2 model must
reproduce its stored metrics on that test set or the script stops. The new games get their own
stratified 80/20 split. A single fresh split of everything would put most of the original test
rows into the new model's training data, and the "original test set" comparison would mean
nothing.

Run from `backend/` against the local training database (the training-only games live there,
so the app never sees them; it exists only on the machine that ingested them):
`DATABASE_URL=postgresql+asyncpg://steam:steam@localhost:5433/steam_reviews_train \
    python scripts/compare_sentiment.py [--adopt]`
"""

import asyncio
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.model_selection import train_test_split

from scripts.train_sentiment import (
    CLASS_NAMES,
    CLASSES,
    RANDOM_SEED,
    SKEW_WARNING_SHARE,
    TEST_SIZE,
    Review,
    build_pipeline,
    drop_empty,
    load_reviews,
    per_class_metrics,
)
from scripts.train_sentiment import (
    REPORT_PATH as SHIPPED_REPORT_PATH,
)

# The five games the M2 model was trained and evaluated on.
ORIGINAL_APPIDS = {620, 949230, 1145360, 1272080, 1716740}
ORIGINAL_N = 5000
PREVIOUS_MODEL_PATH = BACKEND_DIR / "models_store" / "sentiment_m2.joblib"
CANDIDATE_PATH = BACKEND_DIR / "models_store" / "sentiment_candidate.joblib"
CANDIDATE_REPORT_PATH = BACKEND_DIR.parent / "docs" / "eval" / "sentiment_candidate_report.txt"
BOOTSTRAP_RESAMPLES = 1000
BALANCE_SHIFT_WARNING = 0.10  # positive-share change between corpora worth flagging


def split(reviews: list[Review]) -> tuple[list[Review], list[Review]]:
    """The exact call `train_sentiment.train_and_evaluate` makes."""
    return train_test_split(
        reviews, test_size=TEST_SIZE, stratify=[r.voted_up for r in reviews],
        random_state=RANDOM_SEED,
    )


def labels(reviews: list[Review]) -> list[bool]:
    return [r.voted_up for r in reviews]


def predict(model, reviews: list[Review]) -> list[bool]:
    return [bool(p) for p in model.predict([r.text for r in reviews])]


def neg_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    tp = np.sum(~y_true & ~y_pred)
    denom = np.sum(~y_true) + np.sum(~y_pred)
    return 2 * tp / denom if denom else 0.0


def bootstrap_delta(y_true, pred_old, pred_new) -> tuple[float, float]:
    """Paired bootstrap 95% CI of negative F1 (new - old), resampling test reviews."""
    y, a, b = (np.asarray(v, dtype=bool) for v in (y_true, pred_old, pred_new))
    rng = np.random.default_rng(RANDOM_SEED)
    deltas = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        i = rng.integers(0, len(y), len(y))
        deltas.append(neg_f1(y[i], b[i]) - neg_f1(y[i], a[i]))
    return float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))


def metrics_table(add, scores: dict[str, dict]) -> None:
    names = list(scores)
    add(f"  {'class':<10} {'metric':<10} " + " ".join(f"{n:>10}" for n in names))
    for cls in (CLASS_NAMES[c] for c in CLASSES):
        for metric in ("precision", "recall", "f1", "support"):
            vals = [scores[n]["classes"][cls][metric] for n in names]
            fmt = "{:>10d}" if metric == "support" else "{:>10.3f}"
            add(f"  {cls:<10} {metric:<10} " + " ".join(fmt.format(v) for v in vals))
    for key, label in (("accuracy", "accuracy"), ("macro_f1", "macro F1")):
        add(f"  {'overall':<10} {label:<10} "
            + " ".join(f"{scores[n][key]:>10.3f}" for n in names))


def verdict(lo: float, hi: float) -> str:
    if lo > 0:
        return "new BETTER (CI above 0)"
    if hi < 0:
        return "new WORSE (CI below 0)"
    return "no clear difference (CI spans 0)"


def main(adopt: bool) -> int:
    from src.ml.constants import SENTIMENT_MODEL_PATH

    model_path, report_path = (
        (SENTIMENT_MODEL_PATH, SHIPPED_REPORT_PATH) if adopt
        else (CANDIDATE_PATH, CANDIDATE_REPORT_PATH)
    )

    reviews, n_excluded = drop_empty(asyncio.run(load_reviews()))
    orig = [r for r in reviews if r.appid in ORIGINAL_APPIDS]
    new = [r for r in reviews if r.appid not in ORIGINAL_APPIDS]
    if len(orig) != ORIGINAL_N:
        print(f"Expected {ORIGINAL_N} reviews of the original games, found {len(orig)}; "
              f"the original test set can't be rebuilt.")
        return 1
    if not new:
        print("No reviews beyond the original games; ingest the training games first.")
        return 1

    # Rebuild the M2 model's test set and prove it is the same one.
    orig_train, orig_test = split(orig)
    previous = joblib.load(PREVIOUS_MODEL_PATH)
    reproduced = per_class_metrics(labels(orig_test), predict(previous["pipeline"], orig_test))
    stored = previous["metrics"]["model"]
    for key in ("accuracy", "macro_f1"):
        if not math.isclose(reproduced[key], stored[key], abs_tol=1e-9):
            print(f"M2 model's {key} on the rebuilt test set is {reproduced[key]:.6f}, "
                  f"stored {stored[key]:.6f}: not the same test rows. Stopping.")
            return 1

    new_train, new_test = split(new)
    train = orig_train + new_train
    held_out = {id(r) for r in orig_test}
    assert not any(id(r) in held_out for r in train), "original test row in new model's training"

    new_model = build_pipeline().fit([r.text for r in train], labels(train))
    baseline = DummyClassifier(strategy="most_frequent").fit([r.text for r in train], labels(train))

    test_sets = {
        "4a expanded test (original + new games)": orig_test + new_test,
        "4b ORIGINAL test (the M2 model's 1000 rows)": orig_test,
        "extra: new games only (unseen by the M2 model)": new_test,
    }
    lines: list[str] = []
    add = lines.append

    add("=== Dataset ===")
    by_game: dict[str, list[bool]] = {}
    for r in reviews:
        by_game.setdefault(f"{'* ' if r.appid in ORIGINAL_APPIDS else '  '}{r.game}", []).append(
            r.voted_up)
    for game, votes in sorted(by_game.items(), key=lambda kv: (not kv[0].startswith("*"), kv[0])):
        share = sum(votes) / len(votes)
        flag = "  <- one-sided" if max(share, 1 - share) >= SKEW_WARNING_SHARE else ""
        add(f"  {game[:34]:<34} {len(votes):>5} reviews  {share:6.1%} positive{flag}")
    add("  (* = original M2 games)")
    add(f"  Excluded empty/whitespace-only reviews: {n_excluded}")
    orig_pos = sum(labels(orig)) / len(orig)
    all_pos = sum(labels(reviews)) / len(reviews)
    add(f"  Original corpus: {len(orig)} reviews, {orig_pos:.1%} positive")
    add(f"  Expanded corpus: {len(reviews)} reviews from {len(by_game)} games, "
        f"{all_pos:.1%} positive")
    if abs(all_pos - orig_pos) >= BALANCE_SHIFT_WARNING:
        add(f"WARNING: positive share moved {all_pos - orig_pos:+.1%} from the original corpus.")
    for name, rows in (("New model train", train), ("Expanded test", orig_test + new_test)):
        c = Counter(labels(rows))
        add(f"  {name}: {len(rows)} ({c[True]} positive / {c[False]} negative)")
    add(f"  M2 model reproduced on the original test set: accuracy "
        f"{reproduced['accuracy']:.3f}, negative F1 "
        f"{reproduced['classes']['negative']['f1']:.3f} (matches its stored metrics)")

    summary = []
    for title, rows in test_sets.items():
        y = labels(rows)
        preds = {"M2": predict(previous["pipeline"], rows),
                 "new": predict(new_model, rows),
                 "majority": predict(baseline, rows)}
        scores = {n: per_class_metrics(y, p) for n, p in preds.items()}
        lo, hi = bootstrap_delta(y, preds["M2"], preds["new"])
        delta = (scores["new"]["classes"]["negative"]["f1"]
                 - scores["M2"]["classes"]["negative"]["f1"])
        add("")
        add(f"=== {title} ===")
        metrics_table(add, scores)
        add(f"  negative F1 change, new - M2: {delta:+.3f}, "
            f"95% bootstrap CI [{lo:+.3f}, {hi:+.3f}]: {verdict(lo, hi)}")
        summary.append((title, scores, delta, lo, hi))
        if title.startswith("4a"):
            expanded_scores = scores

    add("")
    add("=== Per game, expanded test set ===")
    add(f"  {'game':<34} {'n':>4} {'pos%':>6} {'M2 acc':>8} {'new acc':>8} "
        f"{'M2 neg F1':>10} {'new neg F1':>11}")
    rows = orig_test + new_test
    pc, pn = predict(previous["pipeline"], rows), predict(new_model, rows)
    for game in sorted({(r.appid in ORIGINAL_APPIDS, r.game) for r in rows}, reverse=True):
        idx = [i for i, r in enumerate(rows) if (r.appid in ORIGINAL_APPIDS, r.game) == game]
        y = [rows[i].voted_up for i in idx]
        mc = per_class_metrics(y, [pc[i] for i in idx])
        mn = per_class_metrics(y, [pn[i] for i in idx])
        name = f"{'* ' if game[0] else '  '}{game[1]}"[:34]
        add(f"  {name:<34} {len(idx):>4} {sum(y) / len(y):>6.1%} {mc['accuracy']:>8.3f} "
            f"{mn['accuracy']:>8.3f} {mc['classes']['negative']['f1']:>10.3f} "
            f"{mn['classes']['negative']['f1']:>11.3f}")
    add("  Per-game negative F1 rests on few negatives for one-sided games; read it as a hint.")

    add("")
    add("=== Summary: negative F1 (the minority class), M2 -> new ===")
    for title, scores, delta, lo, hi in summary:
        c, n = (scores[k]["classes"]["negative"]["f1"] for k in ("M2", "new"))
        add(f"  {title.split(' (')[0]:<26} {c:.3f} -> {n:.3f} ({delta:+.3f}, "
            f"CI [{lo:+.3f}, {hi:+.3f}]) {verdict(lo, hi)}")

    report = "\n".join(lines)
    print(report)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": new_model,
            "trained_at": datetime.now(UTC).isoformat(),
            "n_train": len(train),
            "metrics": {"model": expanded_scores["new"],
                        "baseline": expanded_scores["majority"]},
        },
        model_path,
    )
    report_path.write_text(
        f"Sentiment model report (M10: 33-game corpus, compared with the M2 model), generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by "
        f"backend/scripts/compare_sentiment.py\n\n{report}\n",
        encoding="utf-8",
    )
    print(f"\nSaved model to {model_path}")
    print(f"Saved report to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(adopt="--adopt" in sys.argv[1:]))
