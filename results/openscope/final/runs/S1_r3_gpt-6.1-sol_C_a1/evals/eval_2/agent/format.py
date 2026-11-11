"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut Unicode text without encoding it to a restricted character set."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
