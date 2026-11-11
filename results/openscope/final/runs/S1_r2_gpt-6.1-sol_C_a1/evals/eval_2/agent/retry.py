"""Bounded retries for transient tool errors."""
import time
from urllib.error import HTTPError

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """Retry network failures, HTTP rate limiting and server errors only."""
    if isinstance(exc, TRANSIENT):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status is None and isinstance(exc, HTTPError):
        status = exc.code
    return isinstance(status, int) and (status in (408, 429) or 500 <= status <= 599)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Call fn at most attempts times, propagating the final error unchanged.

    Executors should supply idempotent calls (or deduplicate notifications), as
    a lost response does not imply that the remote operation failed.
    """
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
