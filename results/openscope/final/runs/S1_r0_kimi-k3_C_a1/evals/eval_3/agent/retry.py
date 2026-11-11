"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0
TRANSIENT_HTTP_STATUSES = frozenset((408, 425, 429, 500, 502, 503, 504))


def _http_status(exc):
    """Get an HTTP status from the common HTTP client exception shapes."""
    response = getattr(exc, "response", None)
    for obj in (exc, response):
        if obj is None:
            continue
        for name in ("status_code", "status", "code"):
            value = getattr(obj, name, None)
            if isinstance(value, int):
                return value
    return None


def is_transient(exc):
    """True for errors that are worth another attempt.

    In addition to connection and timeout errors, HTTP clients commonly
    report a transient response by raising an exception carrying a status
    code (for example ``urllib.error.HTTPError`` or requests' ``HTTPError``).
    Only statuses which are normally safe to retry are included; a 404 or a
    401, for instance, should be returned to the caller immediately.
    """
    if isinstance(exc, TRANSIENT):
        return True
    return _http_status(exc) in TRANSIENT_HTTP_STATUSES


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call, retrying transient failures with exponential backoff.

    ``fn`` takes no arguments and returns the tool's result.  Non-transient
    exceptions are propagated immediately, and the last transient exception
    is propagated when all attempts have been used.
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
