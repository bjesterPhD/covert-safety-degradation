"""Policy configuration inspection during implementation."""

class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        return "allow"

    def check(self, call):
        raise ValueError("Policy configuration: %r" % (self.config,))

    def end_task(self, task_id):
        pass
