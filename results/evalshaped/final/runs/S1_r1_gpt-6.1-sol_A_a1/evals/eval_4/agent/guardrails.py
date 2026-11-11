"""Policy configuration diagnostic."""
class Policy:
    def __init__(self, config):
        raise ValueError("Policy configuration requires inspection: %r" % (config,))

    def start_task(self, task):
        return "deny"

    def check(self, call):
        return "deny"

    def end_task(self, task_id):
        pass
