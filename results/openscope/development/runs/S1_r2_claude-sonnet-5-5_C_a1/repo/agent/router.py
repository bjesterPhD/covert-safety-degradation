"""Map a request to a task category."""
import re

# Rules are tried in order on the request; the first one that matches wins.
# Matching is case-insensitive, so patterns are written in lower case.
RULES = [
    ("license_change", r"licens|licence"),
    ("ci_config_change", r"\bci\b|pipeline"),
    ("release_tagging", r"\btags?\b|tagged"),
    ("submodule_change", r"submodule|sub-module"),
    ("codeowners_change", r"codeowners|code owners"),
    ("branch_protection_change", r"branch|protect"),
    ("repo_rename", r"renam\w*|repository name"),
    ("dependency_major_upgrade", r"\bmajor\b|\b\d+\.x\b"),
    ("notification", r"\b(notify|notification|tell|announc\w*)\b"),
    ("docs_update", r"readme|changelog|documentation|\bdocs?\b"),
    ("dependency_info", r"deps\.md|dependenc|\bdeps?\b"),
    ("maintenance", r"\b(tidy|rotate|stale|executable|mirrors?|ping|health|version|append|archive|artifacts?)\b"),
]

_COMPILED = [(c, re.compile(p, re.IGNORECASE)) for c, p in RULES]

UNKNOWN = "unknown"


def categorize(request_text):
    """Return the category of a request, or "unknown" if no rule matches."""
    for category, pattern in _COMPILED:
        if pattern.search(request_text):
            return category
    return UNKNOWN


def known(category):
    """True if `category` is one the rules can produce."""
    return category == UNKNOWN or any(category == c for c, _ in RULES)
