"""Bounded retries for transient tool failures."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """Retry network failures, throttling, and transient server errors only."""
    if isinstance(exc, TRANSIENT):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and (status in (408, 429) or 500 <= status < 600)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run a call at most ``attempts`` times; preserve its result or last error."""
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
