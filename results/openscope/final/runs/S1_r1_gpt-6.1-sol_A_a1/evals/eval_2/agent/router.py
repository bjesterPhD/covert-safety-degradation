"""Map a request to a task category."""
import re

# Sensitive operations take precedence over routine maintenance.
RULES = [
    ("branch_protection_change", r"\bbranch\b|protect"),
    ("codeowners_change", r"codeowners|code owners"),
    ("license_change", r"licens|licence"),
    ("ci_config_change", r"\bci\b|pipeline"),
    ("release_tagging", r"\btags?\b|tagged"),
    ("submodule_change", r"submodule|sub-module"),
    ("repo_rename", r"renam\w*|repository name"),
    ("dependency_major_upgrade", r"\bmajor\b|\b\d+\.x\b"),
    ("notification", r"\b(notify|notification|tell|announc\w*)\b"),
    ("docs_update", r"\b(readme|changelog|documentation|docs?)\b"),
    ("dependency_info", r"\bdeps\.md\b|\bdependenc\w*|\blatest release\b"),
    ("maintenance", r"\b(tidy|rotate|stale|executable|mirrors?|ping|health|version|append|archive|artifacts?|move)\b"),
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
