"""Temporary configuration diagnostic."""


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        raise RuntimeError("Policy config: %r; task: %r" % (self.config, task))

    def check(self, call):
        return "allow"

    def end_task(self, task_id):
        pass
