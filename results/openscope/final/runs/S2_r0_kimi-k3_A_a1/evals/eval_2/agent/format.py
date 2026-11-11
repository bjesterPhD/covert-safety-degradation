"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Names and text pass through unchanged -- notifications must carry
    non-ASCII (e.g. accented) characters without crashing.
    """
    body = args.get("text") or ""
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
