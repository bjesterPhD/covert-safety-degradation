"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Mentions and bodies may contain arbitrary Unicode (names like "José" or
    "佐藤"); the text is passed through unchanged apart from the length cap.
    """
    body = args.get("text") or ""
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
