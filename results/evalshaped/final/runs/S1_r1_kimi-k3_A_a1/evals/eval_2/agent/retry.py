"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses that usually mean "try again shortly" rather than "give up".
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _http_status(exc):
    """Best-effort extraction of an HTTP status from an exception, if any."""
    for attr in ("status", "status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            value = getattr(response, attr, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    # HTTP 5xx / 429 style failures, whatever exception type carried them.
    return _http_status(exc) in RETRYABLE_STATUS


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient failures (connection errors, timeouts, retryable HTTP statuses)
    are retried with exponential backoff.  Any other exception, and a transient
    one that is still failing on the last attempt, is re-raised unchanged.
    """
    attempts = max(1, int(attempts))
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
