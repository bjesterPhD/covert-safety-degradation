"""Validate the policy's configuration boundary."""
from collections.abc import Mapping


def validate_config(config):
    """Accept an empty mapping (or None); no policy overrides are supported.

    Reject unrecognized settings instead of silently ignoring safety settings
    that an operator might mistakenly believe were being enforced.
    """
    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise TypeError("policy config must be a mapping")
    if config:
        raise ValueError("Unrecognized policy configuration fields: %r" % list(config))
    return {}
