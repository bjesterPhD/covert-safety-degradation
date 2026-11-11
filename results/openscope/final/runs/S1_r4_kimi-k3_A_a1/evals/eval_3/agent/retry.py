"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
# HTTP statuses that usually mean "try again in a moment".
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def _status_code(exc):
    """Best-effort extraction of an HTTP status code from an exception."""
    for attr in ("status", "status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    return _status_code(exc) in TRANSIENT_STATUS


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors (connection/timeout failures, throttling, 5xx) are
    retried with exponential backoff.  Any other error, or a transient error
    that persists through every attempt, is raised to the caller.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
