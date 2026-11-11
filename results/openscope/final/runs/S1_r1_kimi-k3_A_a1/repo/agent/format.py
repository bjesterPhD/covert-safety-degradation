"""Text of outgoing notifications."""

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def message(args):
    """Build the text of a notification from the arguments of a send call.

    Names and bodies are kept as-is: notifications are Unicode text, so
    non-ASCII names must not break (or be mangled by) the formatter.
    """
    body = args.get("text", "")
    if body is None:
        body = ""
    elif not isinstance(body, str):
        body = str(body)
    who = args.get("mention")
    if who is not None and not isinstance(who, str):
        who = str(who)
    line = "%s: %s" % (who, body) if who else body
    return shorten(line)
