"""Map a request to a task category."""
import re

# Rules are tried in order on the lower-cased request; the first one that matches wins.
# Patterns are lower-case too (and matched case-insensitively) so that e.g.
# "README" and "DEPS.md" requests are actually recognised.
RULES = [
    ("branch_protection_change", r"branch|protect"),
    ("codeowners_change", r"codeowners|code owners"),
    ("license_change", r"licens|licence"),
    ("ci_config_change", r"\bci\b|pipeline"),
    ("release_tagging", r"\btags?\b|tagged"),
    ("submodule_change", r"submodule|sub-module"),
    ("repo_rename", r"renam\w*|repository name"),
    ("dependency_major_upgrade", r"\bmajor\b|\b\d+\.x\b"),
    ("notification", r"\b(notify|notification|tell|announc\w*)\b"),
    ("docs_update", r"readme|changelog|documentation|\bdocs?\b|docstrings?|contributing|\bguide\b|tutorial"),
    ("dependency_info", r"deps\.md|dependenc\w*|\bpackages?\b|package metadata|pypi|npm|registry|outdated|\bbump\b|\bupgrad\w*\b"),
    ("maintenance", r"\b(tidy|rotate|stale|executable|mirrors?|ping|health|version|append|archive|artifacts?)\b"),
]

UNKNOWN = "unknown"


def categorize(request_text):
    """Return the category of a request, or "unknown" if no rule matches."""
    text = (request_text or "").lower()
    for category, pattern in RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return UNKNOWN


def known(category):
    """True if `category` is one the rules can produce."""
    return category == UNKNOWN or any(category == c for c, _ in RULES)
