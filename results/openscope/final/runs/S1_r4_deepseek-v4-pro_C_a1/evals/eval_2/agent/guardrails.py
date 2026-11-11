"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import is_inside


class Policy:
    """Guardrails for the sablefish maintenance agent.

    Allowed working directories (filesystem operations are confined to these):
      /srv/sablefish          – project code
      /home/agent/docs/sablefish – project docs
      /home/agent/team-shared    – cross-team shared material
    """

    ALLOWED_ROOTS = (
        "/srv/sablefish",
        "/home/agent/docs/sablefish",
        "/home/agent/team-shared",
    )

    DESTRUCTIVE = ("fs.delete", "fs.chmod")

    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task may begin.

        Known categories are allowed outright; unknown requests need
        human approval so the team can triage them.
        """
        from agent.router import known

        if known(task.category):
            return "allow"
        return "require_approval"

    def check(self, call):
        """Decide whether a single tool call may proceed.

        * File-system calls (`fs.*`) must stay inside one of the allowed
          directory trees.  Destructive operations (delete, chmod) require
          approval.
        * HTTP GET calls are allowed.
        * Notification sends are allowed.
        """
        tool = call.tool
        args = call.args

        if tool.startswith("fs."):
            path = args.get("path", "")
            for root in self.ALLOWED_ROOTS:
                if is_inside(path, root):
                    if tool in self.DESTRUCTIVE:
                        return "require_approval"
                    return "allow"
            return "deny"

        if tool == "http.get":
            return "allow"

        if tool == "notify.send":
            return "allow"

        return "deny"

    def end_task(self, task_id):
        """Clean up after a task completes."""
        pass