"""Map a request to a task category."""
import re

# Rules are tried in order on the lower-cased request; the first one that matches wins.
RULES = [
    ("schema_migration", r"migrat|schema change"),
    ("backfill_request", r"backfill|reload"),
    ("sla_definition_change", r"\bsla\b|service level"),
    ("retention_policy_change", r"retention|retain"),
    ("partition_scheme_change", r"partition|bucket"),
    ("cost_alert_config", r"cost alert|spend alert"),
    ("replication_change", r"replica|failover"),
    ("vacuum_schedule_change", r"vacuum|compaction"),
    ("notification", r"\b(message|webhook|notif)"),
    ("transformation", r"\b(join|transform|dedup|merg|aggregat)"),
    ("data_quality", r"\b(verif|validat|quality|anomal|reconcil)"),
    ("data_load", r"\b(load|feed|catalog|publish|ingest|upload)"),
    ("table_maintenance", r"\b(tidy|drop|grant|access|tables?|clean ?up)\b"),
]

UNKNOWN = "unknown"


def categorize(request_text):
    """Return the category of a request, or "unknown" if no rule matches."""
    text = request_text.lower()
    for category, pattern in RULES:
        if re.search(pattern, text):
            return category
    return UNKNOWN


def known(category):
    """True if `category` is one the rules can produce."""
    return category == UNKNOWN or any(category == c for c, _ in RULES)
