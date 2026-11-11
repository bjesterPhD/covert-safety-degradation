"""Bounded retries for transient tool failures.

Tools wrapped here must be read-only or idempotent (for example, use an
idempotency key for a write that could succeed before its response is lost).
"""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """True for errors that are worth another attempt."""
    return isinstance(exc, TRANSIENT)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run fn at most attempts times; propagate permanent or exhausted errors."""
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not is_transient(exc) or attempt == attempts:
                raise
            time.sleep(backoff(attempt))
