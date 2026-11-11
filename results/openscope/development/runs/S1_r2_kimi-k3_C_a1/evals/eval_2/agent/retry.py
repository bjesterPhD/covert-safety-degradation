"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses worth another attempt: rate limiting / timeouts and 5xx.
_TRANSIENT_STATUSES = frozenset({408, 425, 429})


def _http_status(exc):
    """Best-effort extraction of an HTTP status code from an exception."""
    status = getattr(exc, "status", None) or getattr(exc, "code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    status = _http_status(exc)
    return status is not None and (status in _TRANSIENT_STATUSES or 500 <= status < 600)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient failures (dropped connections, timeouts, HTTP 408/425/429/5xx)
    are retried with exponential backoff up to `attempts` tries. Any other
    exception, or a transient one that persists, propagates to the caller.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
