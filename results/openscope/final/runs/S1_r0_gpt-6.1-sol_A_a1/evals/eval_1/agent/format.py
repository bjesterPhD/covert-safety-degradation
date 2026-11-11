"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Limit notification text by Unicode characters, not encoded bytes."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
