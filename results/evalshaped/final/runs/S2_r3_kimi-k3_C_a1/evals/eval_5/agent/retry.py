"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0


def is_transient(exc):
    """Return whether an error is worth another attempt."""
    return isinstance(exc, TRANSIENT)


def backoff(attempt):
    """Return the seconds to wait after failed attempt 1, 2, 3, ... ."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call, retrying only transient failures.

    ``fn`` takes no arguments.  The last exception is re-raised unchanged when
    the attempt budget is exhausted; non-transient exceptions are never
    retried.
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

    # The loop either returns or raises.  This is only a guard for type
    # checkers and future changes to the loop.
    raise RuntimeError("retry loop ended without a result")
