"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Names and text are kept as-is: notifications are unicode, so non-ASCII
    characters (accents, non-Latin scripts, emoji) must survive untouched.
    """
    body = args.get("text", "")
    if body is None:
        body = ""
    elif not isinstance(body, str):
        body = str(body)
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
