"""Retry wrapper for tool calls."""
import time
from urllib.error import HTTPError

TRANSIENT = (ConnectionError, TimeoutError)
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, HTTPError):
        return exc.code in TRANSIENT_HTTP_CODES
    return isinstance(exc, TRANSIENT)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient failures are retried with exponential backoff.  Other failures
    are raised immediately.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
