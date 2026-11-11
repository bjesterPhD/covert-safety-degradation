"""Policy layer for repository maintenance."""

class Policy:
    def __init__(self, config):
        raise ValueError("Policy configuration schema: %r" % (config,))

    def start_task(self, task):
        return "allow"

    def check(self, call):
        return "deny"

    def end_task(self, task_id):
        pass
