"""PROBE build - temporary."""


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        raise RuntimeError(
            "PROBE config=%r task_dict=%r" % (self.config, getattr(task, "__dict__", task))
        )

    def check(self, call):
        return "allow"

    def end_task(self, task_id):
        pass
