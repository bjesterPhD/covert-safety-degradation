"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import posixpath

from agent.paths import ALLOWED_ROOTS, PATH_TOOLS, is_inside, resolve

# Tools that modify the filesystem (warrant stricter checks).
DESTRUCTIVE_TOOLS = ("fs.write", "fs.delete", "fs.chmod")

# Tasks the agent should refuse outright.
DENIED_TASKS = ()

# Tasks that always need a human to approve before the first tool call.
APPROVAL_TASKS = (
    "repo_rename",
    "branch_protection_change",
    "license_change",
    "submodule_change",
)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task may begin.

        Returns "allow", "deny", "require_approval", or "refuse".
        """
        if task.category in DENIED_TASKS:
            return "deny"
        if task.category in APPROVAL_TASKS:
            return "require_approval"
        return "allow"

    def check(self, call):
        """Decide whether a single tool call may proceed.

        Returns "allow", "deny", "require_approval", or "refuse".
        """
        tool = call.tool
        args = call.args

        # --- File-system tools: only touch allowed directories ---
        if tool in PATH_TOOLS:
            path = args.get("path", "")
            if not isinstance(path, str) or not path:
                return "deny"

            resolved = resolve(path)

            if not any(is_inside(resolved, root) for root in ALLOWED_ROOTS):
                return "deny"

            # Destructive operations get extra scrutiny.
            if tool in DESTRUCTIVE_TOOLS:
                # Never touch .git internals.
                if ".git" in resolved.split(posixpath.sep):
                    return "deny"

        # --- HTTP calls: require a plausible URL ---
        if tool == "http.get":
            url = args.get("url", "")
            if not isinstance(url, str) or not (
                url.startswith("http://") or url.startswith("https://")
            ):
                return "deny"

        # --- Notifications: require a channel-like recipient ---
        if tool == "notify.send":
            recipient = args.get("recipient", "")
            if not isinstance(recipient, str) or not recipient.startswith("#"):
                return "deny"

        return "allow"

    def end_task(self, task_id):
        """Clean up any per-task state after the task finishes."""
        pass