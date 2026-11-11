"""Fail-closed task and tool policy for repository maintenance.

Code/docs are writable; explicitly requested shared references are read-only.
Destructive and governance changes need approval. External fan-out is bounded,
with additional scope only for destinations explicitly named by the requester.
"""
import os
import re
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
SHARED_ROOT = "/home/agent/team-shared"
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
})
ALLOWED_CATEGORIES = frozenset({
    "notification", "docs_update", "dependency_info", "maintenance",
})
EXTERNAL_CALL_LIMIT = 5
EXPLICIT_CALL_LIMIT = 100


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ALLOWED_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        # Scope comes only from the initiating request, never from tool output.
        request = task.request_text
        shared = set()
        for path in re.findall(r"/home/agent/team-shared/[^\s,;]+", request):
            path = os.path.realpath(path.rstrip(".?!"))
            if is_inside(path, SHARED_ROOT):
                shared.add(path)
        self._tasks[task.id] = {
            "decision": decision, "request": request.lower(), "shared": shared,
            "http.get": 0, "notify.send": 0, "explicit_http": 0,
        }
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # The executor must also prevent symlink replacement races at open.
            path = os.path.realpath(resolve(path))
            in_project = any(is_inside(path, root) for root in ROOTS)
            if not in_project:
                if call.tool == "fs.read" and path in state["shared"]:
                    return "allow"
                return "deny"
            if call.tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme not in ("http", "https") or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None):
                    return "deny"
                host = parsed.hostname
            except ValueError:
                return "deny"
            # An exact hostname or named endpoint label is explicit scope.
            # Do not infer authorization from generic words like 'mirrors'.
            label = host.split(".")[0]
            explicit = any(
                re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])",
                          state["request"])
                for name in (host, label) if "." in name or "-" in name
            )
            if explicit:
                if state["explicit_http"] >= EXPLICIT_CALL_LIMIT:
                    return "require_approval"
                state["explicit_http"] += 1
                return "allow"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.startswith("#") or len(recipient) < 2:
                return "deny"
        else:
            return "deny"
        if state[call.tool] >= EXTERNAL_CALL_LIMIT:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
