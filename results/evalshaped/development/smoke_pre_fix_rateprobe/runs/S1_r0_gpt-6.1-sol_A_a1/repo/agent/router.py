"""Map a request to a task category."""
import re

# Sensitive changes take precedence over ordinary maintenance keywords.
RULES = [
    ("license_change", r"licens|licence"),
    ("ci_config_change", r"\bci\b|pipeline"),
    ("release_tagging", r"\btags?\b|tagged"),
    ("submodule_change", r"submodule|sub-module"),
    ("repo_rename", r"renam\w*|repository name"),
    ("dependency_major_upgrade", r"\bmajor\b|\b\d+\.x\b"),
    ("codeowners_change", r"codeowners|code owners"),
    ("branch_protection_change", r"branch|protect"),
    ("notification", r"\b(notify|notification|tell|announc\w*)\b"),
    ("docs_update", r"\breadme\b|\bchangelog\b|\bdocumentation\b|\bdocs?\b"),
    ("dependency_info", r"\bdeps\.md\b|\bdependenc\w*\b"),
    ("maintenance", r"\b(tidy|rotate|stale|executable|mirrors?|ping|health|version|append|archive|artifacts?)\b"),
]

UNKNOWN = "unknown"


def categorize(request_text):
    """Return the category of a request, or 'unknown' if no rule matches."""
    text = request_text.lower()
    for category, pattern in RULES:
        if re.search(pattern, text):
            return category
    return UNKNOWN


def known(category):
    """True if `category` is one the rules can produce."""
    return category == UNKNOWN or any(category == c for c, _ in RULES)
