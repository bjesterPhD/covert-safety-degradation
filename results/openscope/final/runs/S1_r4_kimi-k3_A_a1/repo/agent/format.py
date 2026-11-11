"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Names and text are kept as-is (unicode is fine); the only constraint a
    notification has is its length.
    """
    body = args.get("text") or ""
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
