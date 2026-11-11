"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Limit notifications by Unicode code points, not encoded bytes."""
    return line[:MAX_LENGTH]


def message(args):
    """Build Unicode notification text without a lossy encoding round-trip."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
