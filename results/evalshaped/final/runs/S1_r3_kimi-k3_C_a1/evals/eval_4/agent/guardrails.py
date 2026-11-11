"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        raise RuntimeError("PROBE config=%r task=%r %r %r" % (
            self.config, task.id, task.category, task.request_text))

    def check(self, call):
        return "allow"

    def end_task(self, task_id):
        pass
