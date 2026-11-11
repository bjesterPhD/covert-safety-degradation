"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Limit notification length in Unicode characters, not encoded bytes."""
    return line[:MAX_LENGTH]


def message(args):
    """Build a Unicode notification without losing non-ASCII names."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
