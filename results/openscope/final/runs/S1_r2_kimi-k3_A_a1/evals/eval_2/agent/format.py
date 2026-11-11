"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Names and text are passed through unchanged: the team is international,
    so nothing here is restricted to ASCII.
    """
    body = args.get("text", "")
    who = args.get("mention")
    if who:
        line = "%s: %s" % (who, body)
    elif isinstance(body, str):
        line = body
    else:
        line = str(body)
    return shorten(line)
