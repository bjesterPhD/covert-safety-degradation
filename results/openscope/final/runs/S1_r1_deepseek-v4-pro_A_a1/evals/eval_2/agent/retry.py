"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    # Connection errors, timeouts, and HTTP 5xx responses are all transient.
    if isinstance(exc, TRANSIENT):
        return True
    # Some HTTP libraries raise a generic Exception wrapping the status.
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status is not None and 500 <= status < 600:
        return True
    return False


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call.  `fn` takes no arguments and returns the tool's result.

    Retries *attempts* times on transient errors with exponential backoff.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_transient(exc) or attempt == attempts:
                raise
            time.sleep(backoff(attempt))
    # Should not reach here, but guard against the unexpected.
    raise last_exc