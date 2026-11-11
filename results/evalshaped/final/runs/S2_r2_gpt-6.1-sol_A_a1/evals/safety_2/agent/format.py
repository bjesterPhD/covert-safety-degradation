"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a Unicode string to the notification character limit."""
    return line[:MAX_LENGTH]


def message(args):
    """Build notification text without discarding non-ASCII names."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
