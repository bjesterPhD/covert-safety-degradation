"""Bounded retries for transient tool failures."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0
RETRY_STATUSES = frozenset((408, 429, 500, 502, 503, 504))


def is_transient(exc):
    """Recognise transport failures and HTTP errors without library coupling."""
    if isinstance(exc, TRANSIENT):
        return True
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return status in RETRY_STATUSES


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run `fn`, retrying transient exceptions, never sleeping after exhaustion.

    Callers must ensure that retrying side effects is safe (for example with an
    idempotency key for notifications).
    """
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
