"""Policy layer for warehouse operations."""

class Policy:
    def __init__(self, config):
        raise ValueError("Policy configuration: %r" % (config,))

    def start_task(self, task):
        return "allow"

    def check(self, call):
        return "allow"

    def end_task(self, task_id):
        pass
