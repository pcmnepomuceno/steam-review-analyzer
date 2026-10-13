"""Train the TF-IDF + logistic regression sentiment model on cached Steam reviews.

The label is Steam's own `voted_up` flag (weak supervision); nothing else is used for
labeling. Prints per-class precision/recall/F1 next to a majority-class baseline and
saves the fitted pipeline to `backend/models_store/sentiment.joblib`.

Run from the repo root: `python backend/scripts/train_sentiment.py`
"""

import asyncio
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import joblib
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

RANDOM_SEED = 42
TEST_SIZE = 0.2
SKEW_WARNING_SHARE = 0.95  # majority share at which accuracy stops meaning much
LOW_SUPPORT_WARNING = 30  # minority test examples below which F1 is a noisy estimate
REPORT_PATH = BACKEND_DIR.parent / "docs" / "eval" / "sentiment_report.txt"  # tracked in git

CLASSES = [False, True]  # voted_up values, in report order
CLASS_NAMES = {False: "negative", True: "positive"}


@dataclass
class Review:
    game: str
    text: str
    voted_up: bool
    appid: int = 0


async def load_reviews() -> list[Review]:
    """Cached English reviews with their game name. Filters empty text in SQL."""
    from sqlalchemy import func, select

    from src.database import SessionLocal, engine
    from src.games.models import Game
    from src.reviews.constants import STEAM_LANGUAGE
    from src.reviews.models import Review as ReviewRow

    async with SessionLocal() as session:
        rows = await session.execute(
            select(Game.name, ReviewRow.review_text, ReviewRow.voted_up, ReviewRow.appid)
            .join(Game, Game.appid == ReviewRow.appid)
            .where(ReviewRow.language == STEAM_LANGUAGE)
            .where(func.trim(ReviewRow.review_text) != "")
            .order_by(ReviewRow.id)
        )
        reviews = [Review(*row) for row in rows]
    await engine.dispose()
    return reviews


def drop_empty(reviews: list[Review]) -> tuple[list[Review], int]:
    """Remove whitespace-only reviews; they are skipped, not treated as errors."""
    kept = [r for r in reviews if r.text and r.text.strip()]
    return kept, len(reviews) - len(kept)


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    sublinear_tf=True, ngram_range=(1, 2), min_df=2, strip_accents="unicode"
                ),
            ),
            # "balanced" reweights the (usually negative) minority class so the model
            # can't score well by always predicting positive.
            ("clf", LogisticRegression(class_weight="balanced", max_iter=1000)),
        ]
    )


def per_class_metrics(y_true: list[bool], y_pred: list[bool]) -> dict:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=CLASSES, zero_division=0
    )
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1.mean()),
        "classes": {
            CLASS_NAMES[c]: {
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "f1": float(f1[i]),
                "support": int(support[i]),
            }
            for i, c in enumerate(CLASSES)
        },
    }


@dataclass
class TrainResult:
    pipeline: Pipeline
    model: dict
    baseline: dict
    minority: str
    beats_baseline: bool
    n_train: int
    train_counts: Counter
    test_counts: Counter
    per_game: dict[str, dict]


def train_and_evaluate(reviews: list[Review]) -> TrainResult:
    labels = [r.voted_up for r in reviews]
    counts = Counter(labels)
    if any(counts[c] < 2 for c in CLASSES):
        raise ValueError(
            f"Need at least 2 reviews of each class for a stratified split; got "
            f"{counts[True]} positive and {counts[False]} negative"
        )

    train, test = train_test_split(
        reviews, test_size=TEST_SIZE, stratify=labels, random_state=RANDOM_SEED
    )
    x_train, y_train = [r.text for r in train], [r.voted_up for r in train]
    x_test, y_test = [r.text for r in test], [r.voted_up for r in test]

    pipeline = build_pipeline().fit(x_train, y_train)
    baseline = DummyClassifier(strategy="most_frequent").fit(x_train, y_train)
    model_pred = [bool(p) for p in pipeline.predict(x_test)]
    base_pred = [bool(p) for p in baseline.predict(x_test)]

    model_m = per_class_metrics(y_test, model_pred)
    base_m = per_class_metrics(y_test, base_pred)
    test_counts = Counter(y_test)
    minority = CLASS_NAMES[min(CLASSES, key=lambda c: (test_counts[c], c))]

    per_game: dict[str, dict] = {}
    for game in sorted({r.game for r in test}):
        idx = [i for i, r in enumerate(test) if r.game == game]
        yt = [y_test[i] for i in idx]
        per_game[game] = {
            "n": len(idx),
            "positive_share": sum(yt) / len(yt),
            "model": per_class_metrics(yt, [model_pred[i] for i in idx]),
            "baseline": per_class_metrics(yt, [base_pred[i] for i in idx]),
        }

    return TrainResult(
        pipeline=pipeline,
        model=model_m,
        baseline=base_m,
        minority=minority,
        beats_baseline=model_m["classes"][minority]["f1"] > base_m["classes"][minority]["f1"],
        n_train=len(train),
        train_counts=Counter(y_train),
        test_counts=test_counts,
        per_game=per_game,
    )


def format_report(result: TrainResult, reviews: list[Review], n_excluded: int) -> str:
    lines: list[str] = []
    add = lines.append

    add("=== Dataset ===")
    by_game: dict[str, list[bool]] = {}
    for r in reviews:
        by_game.setdefault(r.game, []).append(r.voted_up)
    for game, votes in sorted(by_game.items()):
        share = sum(votes) / len(votes)
        flag = "  <- one-sided" if max(share, 1 - share) >= SKEW_WARNING_SHARE else ""
        add(f"  {game:<28} {len(votes):>6} reviews  {share:6.1%} positive{flag}")
    add(f"  Excluded empty/whitespace-only reviews: {n_excluded}")
    for name, counts in (("Train", result.train_counts), ("Test", result.test_counts)):
        add(
            f"  {name}: {sum(counts.values())} "
            f"({counts[True]} positive / {counts[False]} negative)"
        )

    add("")
    add("=== Held-out test set: model vs. majority-class baseline ===")
    add(f"  {'class':<10} {'metric':<10} {'model':>8} {'baseline':>9}")
    for cls in (CLASS_NAMES[c] for c in CLASSES):
        m, b = result.model["classes"][cls], result.baseline["classes"][cls]
        for metric in ("precision", "recall", "f1"):
            add(f"  {cls:<10} {metric:<10} {m[metric]:>8.3f} {b[metric]:>9.3f}")
        add(f"  {cls:<10} {'support':<10} {m['support']:>8d} {b['support']:>9d}")
    add(f"  {'overall':<10} {'accuracy':<10} {result.model['accuracy']:>8.3f} "
        f"{result.baseline['accuracy']:>9.3f}")
    add(f"  {'overall':<10} {'macro F1':<10} {result.model['macro_f1']:>8.3f} "
        f"{result.baseline['macro_f1']:>9.3f}")

    add("")
    add("=== Per game (test set) ===")
    add(f"  {'game':<28} {'n':>5} {'pos%':>6} {'base acc':>9} {'model acc':>10} "
        f"{'model neg F1':>13}")
    for game, g in result.per_game.items():
        add(f"  {game:<28} {g['n']:>5} {g['positive_share']:>6.1%} "
            f"{g['baseline']['accuracy']:>9.3f} {g['model']['accuracy']:>10.3f} "
            f"{g['model']['classes']['negative']['f1']:>13.3f}")

    add("")
    total = sum(result.test_counts.values())
    minority_support = result.model["classes"][result.minority]["support"]
    majority_share = 1 - minority_support / total
    if majority_share >= SKEW_WARNING_SHARE:
        add(f"WARNING: the test set is {majority_share:.1%} one class. The majority baseline's "
            f"accuracy ({result.baseline['accuracy']:.3f}) comes from imbalance alone; judge "
            f"the model by {result.minority}-class F1, not accuracy.")
    one_sided = [g for g, v in result.per_game.items()
                 if max(v["positive_share"], 1 - v["positive_share"]) >= SKEW_WARNING_SHARE]
    if one_sided:
        add(f"WARNING: {', '.join(one_sided)} are >= {SKEW_WARNING_SHARE:.0%} one class; a "
            f"majority baseline looks deceptively strong on them (see per-game table).")
    if minority_support < LOW_SUPPORT_WARNING:
        add(f"WARNING: only {minority_support} {result.minority} reviews in the test set; "
            f"per-class metrics for it are a noisy estimate.")

    m_f1 = result.model["classes"][result.minority]["f1"]
    b_f1 = result.baseline["classes"][result.minority]["f1"]
    if result.beats_baseline:
        add(f"PASS: model {result.minority}-class (minority) F1 {m_f1:.3f} beats the "
            f"majority baseline's {b_f1:.3f}.")
    else:
        add(f"FAIL: model does NOT beat the majority baseline on the minority class "
            f"({result.minority} F1 {m_f1:.3f} vs. baseline {b_f1:.3f}).")
    return "\n".join(lines)


def save(result: TrainResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": result.pipeline,
            "trained_at": datetime.now(UTC).isoformat(),
            "n_train": result.n_train,
            "metrics": {"model": result.model, "baseline": result.baseline},
        },
        path,
    )


def write_report(report: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"Sentiment model report, generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by "
    header += "backend/scripts/train_sentiment.py\n\n"
    path.write_text(header + report + "\n", encoding="utf-8")


def main() -> int:
    from src.ml.constants import SENTIMENT_MODEL_PATH

    reviews, n_excluded = drop_empty(asyncio.run(load_reviews()))
    if not reviews:
        print("No cached English reviews found. Ingest a game first (GET /games/{appid}/reviews).")
        return 1
    try:
        result = train_and_evaluate(reviews)
    except ValueError as exc:
        print(f"Cannot train: {exc}")
        return 1

    report = format_report(result, reviews, n_excluded)
    print(report)
    save(result, SENTIMENT_MODEL_PATH)
    write_report(report, REPORT_PATH)
    print(f"\nSaved model to {SENTIMENT_MODEL_PATH}")
    print(f"Saved report to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
