"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
# HTTP statuses that are usually worth another attempt.
TRANSIENT_STATUSES = frozenset([429, 500, 502, 503, 504])
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    # HTTP-style errors carrying a status code (e.g. 503 Service Unavailable).
    status = getattr(exc, "status", getattr(exc, "code", None))
    return status in TRANSIENT_STATUSES


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors are retried with exponential backoff; anything else
    propagates immediately, as does the last failure once attempts run out.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
    raise RuntimeError("unreachable")
