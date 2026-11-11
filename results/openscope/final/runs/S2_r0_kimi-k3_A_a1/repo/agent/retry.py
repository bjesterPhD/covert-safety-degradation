"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
# HTTP statuses a partner API may return that are worth another attempt.
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
    try:
        return int(status) in TRANSIENT_STATUS
    except (TypeError, ValueError):
        return False


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient failures are retried with exponential backoff; any other error
    is raised immediately, as is a transient error that outlasts `attempts`.
    """
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
