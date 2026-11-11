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
    # Keep the text as-is: names and bodies may contain non-ASCII characters,
    # and the webhook handles UTF-8. Just enforce the length limit.
    return shorten(line)
