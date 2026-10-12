from enum import StrEnum

MAX_APPID = 2**31 - 1  # `games.appid` is a 32-bit integer column

SEARCH_CACHE_TTL_S = 60.0  # /steam/search answers per term
SEARCH_CACHE_MAX_TERMS = 500


class AspectStatus(StrEnum):
    """`games.aspects_status`: where the sentiment + aspect pass stands for a game."""

    PENDING = "pending"  # not analyzed yet; the next /aspects request starts it
    PROCESSING = "processing"  # claimed by a worker
    DONE = "done"
    FAILED = "failed"  # reason in `games.aspects_error`; retried by `--force` or a user-asked POST /retry
