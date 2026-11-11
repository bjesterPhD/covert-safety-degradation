"""Map a request to a task category."""
import re

# Rules are tried in order on the lower-cased request; the first one that matches wins.
RULES = [
    ("schema_migration", r"migrat|schema change"),
    ("backfill_request", r"backfill|reload"),
    ("partition_scheme_change", r"partition|bucket"),
    ("sla_definition_change", r"\bsla\b|service level"),
    ("retention_policy_change", r"retention|retain"),
    ("cost_alert_config", r"cost alert|spend alert"),
    ("replication_change", r"replica|failover"),
    ("vacuum_schedule_change", r"vacuum|compaction"),
    ("notification", r"\b(message|webhook)\b"),
    ("transformation", r"\bJoin\b|\bDeduplicate\b"),
    ("data_quality", r"\bVerify\b|\bValidate\b"),
    ("data_load", r"\b(load|feed|catalogue|publish)\b"),
    ("table_maintenance", r"\b(tidy|drop|grant|access|tables?)\b"),
]

UNKNOWN = "unknown"


def categorize(request_text):
    """Return the category of a request, or "unknown" if no rule matches."""
    text = request_text.lower()
    for category, pattern in RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return UNKNOWN


def known(category):
    """True if `category` is one the rules can produce."""
    return category == UNKNOWN or any(category == c for c, _ in RULES)
