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
    # Safely truncate to MAX_LENGTH bytes after encoding, without crashing
    # on non-ASCII characters.
    encoded = line.encode("utf-8")
    if len(encoded) > MAX_LENGTH:
        # Truncate at byte boundary; replace any incomplete multi-byte sequence
        encoded = encoded[:MAX_LENGTH]
        line = encoded.decode("utf-8", errors="replace")
    return line