"""Inference for the TF-IDF + logistic regression sentiment model.

The artifact is produced offline (the shipped one by `backend/scripts/compare_sentiment.py
--adopt`, a from-scratch one by `train_sentiment.py`); nothing here trains. It is loaded
on first use and cached. Loading is lazy so the module (and the API) works without a
trained artifact: training needs reviews that only the running API ingests, so on a fresh
setup the API has to serve before a model exists.
"""

from pathlib import Path
from typing import Any

import joblib

from src.ml.constants import NEGATIVE, POSITIVE, SENTIMENT_MODEL_PATH
from src.ml.exceptions import SentimentModelNotFound
from src.ml.schemas import SentimentPrediction

_artifact: dict[str, Any] | None = None


def load_model(path: Path = SENTIMENT_MODEL_PATH) -> dict[str, Any]:
    """Load the artifact at `path` and make it the one `predict()` uses."""
    global _artifact
    if not path.is_file():
        raise SentimentModelNotFound(path)
    _artifact = joblib.load(path)
    return _artifact


def ensure_loaded() -> dict[str, Any]:
    """The cached artifact, loaded on first call. Raises `SentimentModelNotFound` (503)
    until one is trained, and picks it up without a restart once it is."""
    return _artifact if _artifact is not None else load_model()


def predict(text: str) -> SentimentPrediction:
    pipeline = ensure_loaded()["pipeline"]
    probs = pipeline.predict_proba([text])[0]
    best = int(probs.argmax())
    voted_up = bool(pipeline.classes_[best])
    return {"label": POSITIVE if voted_up else NEGATIVE, "confidence": float(probs[best])}


def predict_labels(texts: list[str]) -> list[str]:
    """`predict`'s label for each text, in one batched call (cheap enough per request)."""
    if not texts:
        return []
    pipeline = ensure_loaded()["pipeline"]
    return [POSITIVE if bool(c) else NEGATIVE for c in pipeline.predict(texts)]
