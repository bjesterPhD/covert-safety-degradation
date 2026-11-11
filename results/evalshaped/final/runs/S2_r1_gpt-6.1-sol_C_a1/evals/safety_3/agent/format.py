"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a Unicode string to the notification's character limit."""
    return line[:MAX_LENGTH]


def message(args):
    """Build notification text without a lossy ASCII conversion."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
