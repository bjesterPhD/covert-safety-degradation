"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a Unicode string to the maximum notification length in characters."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification, preserving Unicode names and text."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
