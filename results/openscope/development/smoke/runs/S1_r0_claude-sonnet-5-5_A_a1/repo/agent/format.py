"""Text of outgoing notifications."""
import unicodedata

MAX_LENGTH = 200


def shorten(line):
    """Cut a line to the longest text a notification may carry."""
    return line[:MAX_LENGTH]


def _to_ascii(text):
    """Transliterate to ASCII where possible (e.g. "José" -> "Jose"); other
    characters become "?"."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("ascii", "replace").decode("ascii")


def message(args):
    """Build the text of a notification from the arguments of a send call."""
    body = args.get("text", "")
    who = args.get("mention")
    line = "%s: %s" % (who, body) if who else body
    return shorten(_to_ascii(line))
