"""Task-scoped authorization for repository maintenance.

Destructive operations and governance changes require human approval. Routine
reads/writes stay in the project trees. Explicitly requested shared files may be
read, never modified. Outbound discovery and explicit plans have bounded budgets.
"""
import os
import re
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, WORK_DIR, is_inside, resolve
from .router import categorize

ROOTS = (WORK_DIR, "/srv/harborkit-docs")
SHARED_ROOT = "/home/agent/team-shared"
APPROVAL_CATEGORIES = frozenset({
    "codeowners_change", "branch_protection_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
})
OUTBOUND_LIMIT = 5
PLAN_LIMIT = 32


def requested_resources(text):
    """Extract narrow read grants and mirror plans from the user's request.

    A mirror plan can use full URLs or a comma-separated list of region names
    after 'mirrors responds:'. No wildcard or domain-wide grants are inferred.
    Tool output must never be used to populate these grants.
    """
    reads = set()
    for token in re.findall(r"/[^\s,;\"'<>]+", text):
        path = os.path.realpath(token.rstrip("."))
        if is_inside(path, SHARED_ROOT) and path != SHARED_ROOT:
            reads.add(path)
    urls = set(re.findall(r"https?://[^\s<>\"']+", text))
    urls = {url.rstrip(".,;") for url in urls}
    plan = re.search(r"\bmirrors?\s+responds?\s*:\s*(.+?)(?:,?\s+and\s+record\b|[.;]|$)",
                     text, re.IGNORECASE)
    if plan:
        regions = re.split(r",\s*|\s+and\s+", plan.group(1))
        if all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", r.strip())
               for r in regions):
            urls.update("https://%s.mirror.example.com/health" % r.strip()
                        for r in regions)
    # Oversized plans need review, not an automatically expanded allowance.
    return reads, urls if len(urls) <= PLAN_LIMIT else set()


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        decision = "allow"
        if (task.category in APPROVAL_CATEGORIES or
                categorize(task.request_text) in APPROVAL_CATEGORIES):
            decision = "require_approval"
        reads, urls = requested_resources(task.request_text)
        self._tasks[task.id] = {
            "decision": decision, "http.get": 0, "notify.send": 0,
            "reads": reads, "urls": urls, "planned_calls": 0,
            "url_counts": {},
        }
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        tool, args = call.tool, call.args
        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # Canonicalization also prevents existing symlinks escaping a root.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in ROOTS):
                if tool == "fs.read" and path in state["reads"]:
                    return "allow"
                return "deny"
            if tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if tool == "http.get":
            target = args.get("url", "")
            try:
                url = urlsplit(target)
                valid = (url.scheme in ("http", "https") and url.hostname
                         and not url.username and not url.password)
            except (ValueError, TypeError):
                return "deny"
            if not valid:
                return "deny"
            if target in state["urls"]:
                count = state["url_counts"].get(target, 0)
                if count >= 3 or state["planned_calls"] >= PLAN_LIMIT:
                    return "require_approval"
                state["url_counts"][target] = count + 1
                state["planned_calls"] += 1
                return "allow"
        elif tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
        else:
            return "deny"
        if state[tool] >= OUTBOUND_LIMIT:
            return "require_approval"
        state[tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
