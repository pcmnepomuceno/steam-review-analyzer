from enum import StrEnum

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
APPREVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
STORESEARCH_URL = "https://store.steampowered.com/api/storesearch/"

STEAM_PAGE_SIZE = 100  # max num_per_page Steam accepts
STEAM_LANGUAGE = "english"

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = 1.0  # doubled after each failed attempt

INGEST_LOCK_NAMESPACE = 1  # first key of the two-int Postgres advisory lock
PROCESS_LOCK_NAMESPACE = 2  # held by a live processing run (service.process_reviews)
INSERT_CHUNK_SIZE = 500

# Example sentences per aspect in /aspects: the highest-similarity units within these
# lengths (shorter ones are mostly "Great story."; longer ones are rambling run-ons).
QUOTES_PER_ASPECT = 3
QUOTE_MIN_FROM_PREFERRED = 2  # fewer than this from the preferred side -> fill from the other
QUOTE_CANDIDATES = 10  # per aspect and sentiment, before dropping duplicate texts
QUOTE_MIN_CHARS = 30
QUOTE_MAX_CHARS = 220
# An aspect's examples lean negative when it is a weak spot: under half positive, or this
# many points below the game's overall share. A 94% aspect in a 95% game is no weak spot.
QUOTE_WEAK_MARGIN_PCT = 5.0


class ReviewSort(StrEnum):
    """Orders for GET /games/{appid}/reviews; ties fall back to newest first."""

    NEWEST = "newest"
    HELPFUL = "helpful"  # Steam's votes_up: other players marked it helpful
    PLAYTIME = "playtime"  # longest playtime_forever first


class Skipped(StrEnum):
    """Why `service.process_reviews` did nothing for an app."""

    NOT_INGESTED = "not ingested"
    ALREADY_DONE = "already processed; --force redoes it"
    RUNNING = "another process is processing it right now; --force can't take over a live run"
    INTERRUPTED = "left 'processing' by a run that stopped; --force retries it"
    FAILED = "last run failed (see games.aspects_error); --force retries it"


# The 500 detail for a failed run whose stored reason isn't INTERRUPTED_ERROR.
GENERIC_FAILURE = (
    "Analysis failed. It needs a manual retry by the site owner; reloading won't change "
    "it. If you're a visitor seeing this, contact the site owner."
)

# games.aspects_error for a run whose process died in the middle of it (see
# service.fail_interrupted_runs).
# Every place that shows it (API 500, CLI skip message) already names the --force retry.
INTERRUPTED_ERROR = (
    "interrupted: the process running it stopped mid-run (out of memory, Ctrl-C, redeploy "
    "or shutdown)"
)
