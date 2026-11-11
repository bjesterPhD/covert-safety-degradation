"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    # Encode to UTF-8 then decode as ASCII with replacement so that non-ASCII
    # characters (e.g. accented names) are replaced rather than crashing.
    return line.encode("utf-8").decode("ascii", errors="replace")[:MAX_LENGTH]