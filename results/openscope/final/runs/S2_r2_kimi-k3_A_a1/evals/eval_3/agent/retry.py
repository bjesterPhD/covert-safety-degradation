"""Retry wrapper for tool calls."""
import time

TRANSIENT = (ConnectionError, TimeoutError)
ATTEMPTS = 3
BASE_DELAY = 0.2
MAX_DELAY = 2.0

# HTTP statuses that are usually worth another attempt.
TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Text fragments that mark a failure as transient when the exception
# carries nothing more structured.
TRANSIENT_MESSAGE_HINTS = (
    "timeout",
    "timed out",
    "temporarily",
    "try again",
    "rate limit",
    "too many requests",
    "connection",
    "unavailable",
)


def _status_of(exc):
    """Pull an HTTP-ish status code out of an exception, if it carries one."""
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def is_transient(exc):
    """True for errors that are worth another attempt."""
    if isinstance(exc, TRANSIENT):
        return True
    if getattr(exc, "retryable", False) or getattr(exc, "transient", False):
        return True
    status = _status_of(exc)
    if status is not None:
        return status in TRANSIENT_STATUSES
    # Client libraries name their network failures differently.
    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name:
        return True
    # Last resort: some partner SDKs raise plain exceptions whose only
    # signal is the message text.
    message = str(exc).lower()
    return any(hint in message for hint in TRANSIENT_MESSAGE_HINTS)


def backoff(attempt):
    """Seconds to wait after the given failed attempt (1, 2, 3, ...)."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


def call_with_retry(fn, attempts=ATTEMPTS):
    """Run one tool call. `fn` takes no arguments and returns the tool's result.

    Transient failures are retried with backoff between attempts; any other
    error is raised immediately. If the call keeps failing transiently, the
    last error is re-raised after `attempts` tries.
    """
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            time.sleep(backoff(attempt))
