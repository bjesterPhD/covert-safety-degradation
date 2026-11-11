"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses that justify another attempt: timeouts, rate limiting,
# and server-side errors. Other 4xx statuses will not fix themselves.
TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _status_of(exc):
    """Best-effort extraction of an HTTP status code from an exception."""
    for attr in ("status", "code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    return _status_of(exc) in TRANSIENT_STATUSES


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors are retried with exponential backoff; any other error
    is raised immediately, as is a transient error on the final attempt.
    """
    if attempts < 1:
        attempts = 1
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
