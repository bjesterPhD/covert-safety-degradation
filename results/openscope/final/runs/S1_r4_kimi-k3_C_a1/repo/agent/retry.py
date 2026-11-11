"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
# HTTP response statuses that may succeed on a later attempt.
TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    # HTTP-style errors: a 5xx (or 408/429) response may succeed on a retry.
    for attr in ("status_code", "status", "code"):
        status = getattr(exc, attr, None)
        if isinstance(status, int):
            return status in TRANSIENT_STATUS_CODES
    return False


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors (see `is_transient`) are retried with exponential
    backoff, up to `attempts` runs in total. Any other error, or a
    transient error on the final attempt, is re-raised.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
