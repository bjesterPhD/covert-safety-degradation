"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
# HTTP status codes that usually mean "try again in a moment".
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    # HTTP-style errors may carry their status on the exception object.
    for attr in ("status", "status_code", "code"):
        if getattr(exc, attr, None) in TRANSIENT_STATUS:
            return True
    return False


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors are retried with exponential backoff; any other error,
    or a transient one that persists past `attempts` tries, propagates.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
