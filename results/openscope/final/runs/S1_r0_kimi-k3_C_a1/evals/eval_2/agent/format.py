"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Notification text is Unicode.  Encoding it as ASCII used to make a
    perfectly valid name such as ``Amélie`` abort the whole task; slicing the
    Python string both preserves the name and enforces the length limit.
    """
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    if not isinstance(line, str):
        line = str(line)
    return shorten(line)
