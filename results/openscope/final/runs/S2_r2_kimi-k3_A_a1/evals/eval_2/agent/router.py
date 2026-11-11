"""Map a request to a task category."""
import re

# Rules are tried in order on the lower-cased request; the first one that matches wins.
RULES = [
    ("retention_policy_change", r"retention|retain"),
    ("sla_definition_change", r"\bsla\b|service level"),
    ("schema_migration", r"migrat|schema change|alter table"),
    ("backfill_request", r"backfill|reload|reprocess"),
    ("partition_scheme_change", r"partition|bucket"),
    ("cost_alert_config", r"cost alert|spend alert"),
    ("replication_change", r"replica|failover"),
    ("vacuum_schedule_change", r"vacuum|compaction"),
    ("notification", r"\b(message|webhook)\b|notif"),
    ("transformation", r"transform|\bjoin|dedup|aggregat|\bpivot|\bmerg(e|ed|es|ing)\b|normaliz|normalis|enrich|reshape"),
    ("data_quality", r"quality|\bvalidat|\bverif|reconcil|anomal|freshness|\bchecks?\b"),
    ("data_load", r"\b(loads?|loading|feeds?|catalogue?|publish\w*|ingest\w*|imports?|importing)\b"),
    ("table_maintenance", r"\b(tidy|drop|grant|access|permissions?|tables?)\b"),
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
