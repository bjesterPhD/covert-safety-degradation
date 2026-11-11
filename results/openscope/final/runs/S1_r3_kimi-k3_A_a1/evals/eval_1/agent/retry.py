"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses that usually mean "try again in a moment".
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status in TRANSIENT_STATUSES


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors are retried with exponential backoff; the last error is
    re-raised once `attempts` is exhausted. Non-transient errors raise
    immediately.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
    raise RuntimeError("call_with_retry: attempts must be >= 1")
