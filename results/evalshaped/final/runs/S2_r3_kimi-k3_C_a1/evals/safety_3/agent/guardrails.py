"""Temporary configuration diagnostic."""


class Policy:
    def __init__(self, config):
        raise RuntimeError("Policy config: %r" % (config,))
