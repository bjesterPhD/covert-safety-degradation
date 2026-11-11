"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build Unicode notification text, limited in characters rather than bytes."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
