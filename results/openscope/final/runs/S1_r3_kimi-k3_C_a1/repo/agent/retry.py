"""Retry wrapper for tool calls."""
import time

try:
    from urllib.error import URLError
except ImportError:  # pragma: no cover - urllib is stdlib
    URLError = ()

TRANSIENT = (ConnectionError, TimeoutError, URLError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses that usually mean "try again in a moment".
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def _status_code(exc):
    """Best-effort extraction of an HTTP status code from an exception.

    Works for urllib's HTTPError (`.code`), requests' HTTPError
    (`.response.status_code`) and simple custom errors (`.status` or
    `.status_code`).
    """
    for attr in ("code", "status", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    status = _status_code(exc)
    if status is not None:
        # An HTTP error is transient only for statuses like 503 or 429;
        # a 404 or 401 will not fix itself.
        return status in TRANSIENT_STATUSES
    return isinstance(exc, TRANSIENT)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call, retrying transient failures with backoff.

    `fn` takes no arguments and returns the tool's result.  Non-transient
    errors are raised immediately; a transient error is re-raised once
    `attempts` tries have been used up.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
