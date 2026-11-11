"""Retry wrapper for tool calls."""
import time
from urllib.error import HTTPError, URLError

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses that are usually worth another attempt.
TRANSIENT_STATUS = frozenset({408, 425, 429})


def _status_code(exc):
    """Best-effort extraction of an HTTP status from an exception."""
    if isinstance(exc, HTTPError):
        return exc.code
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    status = _status_code(exc)
    if status is not None:
        # 5xx means the server had a bad moment; 408/425/429 ask us to retry.
        return status >= 500 or status in TRANSIENT_STATUS
    if isinstance(exc, HTTPError):
        return False
    if isinstance(exc, URLError):
        # No HTTP status: the request never got a real response.
        reason = getattr(exc, "reason", None)
        if isinstance(reason, BaseException):
            return is_transient(reason)
        return True
    return False


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient failures are retried with exponential backoff; anything else is
    raised straight away.  The last error is re-raised once attempts run out.
    """
    if attempts is None:
        attempts = ATTEMPTS
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
    return None  # unreachable: the loop either returns or raises
