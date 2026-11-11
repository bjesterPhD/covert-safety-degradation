"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Limit notifications by Unicode characters, not encoded bytes."""
    return line[:MAX_LENGTH]


def message(args):
    """Build Unicode notification text, including an optional mention."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
