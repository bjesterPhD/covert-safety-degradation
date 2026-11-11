"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    The text is passed through unchanged (the webhook accepts UTF-8); it is
    only cut down to the maximum length.
    """
    body = args.get("text") or ""
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else "%s" % (body,)
    return shorten(line)
