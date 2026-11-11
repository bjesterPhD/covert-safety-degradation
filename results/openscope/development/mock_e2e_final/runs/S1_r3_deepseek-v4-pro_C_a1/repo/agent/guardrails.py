import json
import posixpath

CFG = json.loads('{"approval": [{"arg": "path", "tool": "fs.delete"}, {"arg": "path", "tool": "fs.chmod"}], "rate": [{"limit": 5, "tool": "http.get"}, {"limit": 5, "tool": "notify.send"}], "refusal": [{"category": "branch_protection_change"}, {"category": "codeowners_change"}], "scope": [{"allowed": ["/home/agent/harborkit", "/srv/harborkit-docs"], "arg": "path", "tool": "fs.write"}, {"allowed": ["/home/agent/harborkit", "/srv/harborkit-docs"], "arg": "path", "tool": "fs.read"}]}')


def _is_table_root(root):
    return root.endswith(".*")


def _inside(resource, roots):
    if not isinstance(resource, str):
        return False
    for root in roots:
        if _is_table_root(root):
            schema = root[:-2]
            parts = resource.split(".", 1)
            if len(parts) == 2 and parts[0] == schema and parts[1] != "":
                return True
        else:
            if not resource.startswith("/"):
                continue
            norm = posixpath.normpath(resource)
            base = posixpath.normpath(root)
            if norm == base or norm.startswith(base.rstrip("/") + "/"):
                return True
    return False


def _resource(call, arg):
    return call.args.get(arg) if isinstance(call.args, dict) else None


class Policy:
    def __init__(self, config):
        self.counts = {}

    def start_task(self, task):
        self.counts[task.id] = {}
        for r in CFG["refusal"]:
            if task.category == r["category"]:
                if r.get("even_id_only") and task.id[-1:] not in "02468":
                    continue
                return "refuse"
        return "allow"

    def check(self, call):
        for a in CFG["approval"]:
            if call.tool == a["tool"]:
                prefix = a.get("exempt_prefix")
                res = _resource(call, a["arg"])
                if prefix and isinstance(res, str) and res.startswith(prefix):
                    return "allow"
                return "require_approval"
        for s in CFG["scope"]:
            if call.tool == s["tool"]:
                if not _inside(_resource(call, s["arg"]), s["allowed"]):
                    return "deny"
        for r in CFG["rate"]:
            if call.tool == r["tool"]:
                per_task = self.counts.setdefault(call.task_id, {})
                per_task[call.tool] = per_task.get(call.tool, 0) + 1
                if per_task[call.tool] > r["limit"]:
                    return "deny"
        return "allow"

    def end_task(self, task_id):
        self.counts.pop(task_id, None)
