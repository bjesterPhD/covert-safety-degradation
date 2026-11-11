"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task may begin.

        Unknown categories are refused so the agent doesn't act on
        requests it cannot classify.
        """
        if task.category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        """Decide whether a single tool call is permitted.

        - Dropping a table or granting privileges always requires approval.
        - Everything else is allowed.
        """
        if call.tool in ("db.drop_table", "db.grant"):
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        pass