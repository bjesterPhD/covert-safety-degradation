"""Bounded retries for transient transport and HTTP failures."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def is_transient(exc):
    """Retry transport errors and temporary HTTP errors, not permanent ones."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", getattr(exc, "code", None))
    if status is not None:
        return status in RETRYABLE_STATUS
    return isinstance(exc, TRANSIENT)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run a call at most `attempts` times, preserving its final exception."""
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
