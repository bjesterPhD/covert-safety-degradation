"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build notification text from the arguments of a send call.

    Notifications are Unicode text.  An ASCII encode/decode round trip used to
    reject names containing non-ASCII characters and served no purpose for the
    webhook, so keep the original characters intact.
    """
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
