"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    The webhook accepts UTF-8, so names with non-ASCII characters are kept
    as-is; the text is only truncated to the notification length limit.
    """
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
