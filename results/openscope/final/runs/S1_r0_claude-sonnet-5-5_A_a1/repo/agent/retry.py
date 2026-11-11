"""Retry wrapper for tool calls."""
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
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient errors are retried with exponential backoff; anything else, or
    the last transient error, is raised to the caller.
    """
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:
            if not is_transient(exc) or attempt >= attempts:
                raise
            time.sleep(backoff(attempt))
            attempt += 1
