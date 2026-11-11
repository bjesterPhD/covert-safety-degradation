"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Names and text are kept as-is (unicode included); the result is only
    truncated to the maximum notification length.
    """
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return line[:MAX_LENGTH]
