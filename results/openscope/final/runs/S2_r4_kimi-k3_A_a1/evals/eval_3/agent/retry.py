"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True

    # Partner SDKs often expose an HTTP status instead of raising one of the
    # standard transient exception types.  Rate limiting and server-side
    # failures are safe to retry; client-side errors are not.
    status = getattr(exc, "status_code", getattr(exc, "status", None))
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status == 429 or 500 <= status < 600


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
