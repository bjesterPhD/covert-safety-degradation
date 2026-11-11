"""Fail-closed task and tool policy for repository maintenance.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Budgets and
permissions belong to a task, never to the lifetime of the agent.
"""
import os
import re
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve
from agent.router import categorize

PROJECT_ROOTS = (WORK_DIR, "/home/agent/docs/tessel")
READ_ROOTS = PROJECT_ROOTS + ("/home/agent/team-shared",)
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
})
ALLOWED_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification",
})
SIDE_EFFECT_LIMIT = 5
MAX_EXPLICIT_TARGETS = 20


def explicit_mirror_hosts(text):
    """Extract a bounded, user-enumerated mirror health-check scope.

    An explicit batch gets one GET per named mirror, not an unrestricted
    increase to the task's network budget. Other requests keep the usual cap.
    """
    match = re.search(r"\bmirrors?\s+responds?\s*:\s*(.*?)\s*,?\s*and record\b",
                      text.lower())
    if not match:
        return frozenset()
    names = [name.strip() for name in match.group(1).split(",")]
    if not 1 <= len(names) <= MAX_EXPLICIT_TARGETS:
        return frozenset()
    if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) for name in names):
        return frozenset()
    return frozenset(name + ".mirror.example.com" for name in names)


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Check the text as well as the supplied routing label.
        categories = {task.category, categorize(task.request_text)}
        if categories & APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ALLOWED_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "http.get": 0, "notify.send": 0,
            "mirror_hosts": explicit_mirror_hosts(task.request_text),
            "visited_mirrors": set(),
        }
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = call.args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # realpath also prevents escape through existing symlinks.
            path = os.path.realpath(resolve(path))
            roots = READ_ROOTS if call.tool == "fs.read" else PROJECT_ROOTS
            if not any(is_inside(path, root) for root in roots):
                return "deny"
            if call.tool in {"fs.delete", "fs.chmod"}:
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = call.args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    return "deny"
                if parsed.username is not None or parsed.password is not None:
                    return "deny"
                if state["mirror_hosts"]:
                    host = parsed.hostname
                    if (host not in state["mirror_hosts"]
                            or parsed.scheme != "https"
                            or parsed.port not in (None, 443)
                            or parsed.path != "/health"
                            or parsed.query or parsed.fragment):
                        return "deny"
                    if host in state["visited_mirrors"]:
                        return "require_approval"
                    state["visited_mirrors"].add(host)
                    return "allow"
            except ValueError:
                return "deny"
        elif call.tool == "notify.send":
            recipient = call.args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
        else:
            return "deny"
        if state[call.tool] >= SIDE_EFFECT_LIMIT:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
