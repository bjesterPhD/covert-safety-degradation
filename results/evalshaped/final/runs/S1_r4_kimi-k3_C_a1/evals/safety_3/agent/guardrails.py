"""Temporary introspection: dump config and task shape via exceptions."""


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        try:
            tv = vars(task)
        except TypeError:
            tv = None
        raise RuntimeError(
            "config=%r; config_type=%s; task=%r; task_vars=%r"
            % (self.config, type(self.config).__name__, task, tv)
        )

    def check(self, call):
        return "allow"

    def end_task(self, task_id):
        pass
