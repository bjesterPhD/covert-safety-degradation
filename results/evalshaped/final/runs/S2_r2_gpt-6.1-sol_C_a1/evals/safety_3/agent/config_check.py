"""Configuration validation helpers for the policy."""


def validate_config(config):
    """Reject unrecognized configuration rather than silently ignoring it."""
    if config:
        raise ValueError("Unrecognized policy configuration fields: %r" % list(config))
    return {}
