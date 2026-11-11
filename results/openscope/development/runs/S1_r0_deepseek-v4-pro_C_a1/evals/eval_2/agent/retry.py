"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
HTTP_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


class HTTPError(Exception):
    """An HTTP error with a status code."""
    def __init__(self, status, message=""):
        self.status = status
        super().__init__(message or "HTTP %d" % status)


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    if isinstance(exc, HTTPError):
        return exc.status in HTTP_TRANSIENT_STATUSES
    return False


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_transient(exc) or attempt == attempts:
                raise
            time.sleep(backoff(attempt))
    raise last_exc