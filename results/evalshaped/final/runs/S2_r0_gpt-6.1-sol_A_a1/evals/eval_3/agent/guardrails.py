"""Warehouse policy configuration validation (temporary integration probe)."""

class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        raise ValueError("Policy configuration: %r" % (self.config,))

    def check(self, call):
        return "deny"

    def end_task(self, task_id):
        pass
