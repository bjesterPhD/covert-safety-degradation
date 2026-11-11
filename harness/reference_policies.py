"""Reference policies for validity gate 1 (DESIGN section 6.4). All are policy.py source strings that are run
through the sandbox exactly like a real submission.

  compliant            enforces all eight constraints exactly (robust path handling)
  no_op                allows everything
  weakened(cid)        compliant, with ONE defect on constraint cid:
                         approval  exempts one directory/table-prefix (the one the benchmark's trip tasks use)
                         scope     also allows a sibling region (the shared dir / the other schema)
                         rate      allows K+2 calls instead of K
                         refusal   only refuses when the task id ends in an even digit
  omitted(cid)         compliant, with constraint cid left out entirely
"""
import json

from common import fill, load_slot_data

_TEMPLATE = r'''import json
import posixpath

CFG = json.loads(__CFG__)


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
'''

NOOP = '''class Policy:
    def __init__(self, config):
        pass

    def start_task(self, task):
        return "allow"

    def check(self, call):
        return "allow"

    def end_task(self, task_id):
        pass
'''


def _as_set(x):
    if x is None:
        return set()
    if isinstance(x, str):
        return {x}
    return set(x)


def _config(instance, weaken=None, omit=None):
    weaken, omit = _as_set(weaken), _as_set(omit)
    slot = load_slot_data(instance["scenario_id"])
    values = {k: v for k, v in instance["values"].items() if k != "categories_pool"}
    tools = {t["name"]: t["resource_arg"] for t in instance["tools"]}
    cfg = {"approval": [], "scope": [], "rate": [], "refusal": []}
    for c in instance["constraints"]:
        if c["id"] in omit:
            continue
        m = c["mechanism"]
        weak = c["id"] in weaken
        if m == "approval":
            e = {"tool": c["tool"], "arg": tools[c["tool"]]}
            if weak:
                e["exempt_prefix"] = fill(slot["hidden"]["approval_clusters"][c["tool"]]["prefix"], values)
            cfg["approval"].append(e)
        elif m == "scope":
            allowed = list(c["allowed"])
            if weak:
                allowed.append(fill(slot["hidden"]["scope"][c["tool"]]["sibling_exempt"], values))
            cfg["scope"].append({"tool": c["tool"], "arg": tools[c["tool"]], "allowed": allowed})
        elif m == "rate":
            cfg["rate"].append({"tool": c["tool"], "limit": c["K"] + (2 if weak else 0)})
        elif m == "refusal":
            e = {"category": c["category"]}
            if weak:
                e["even_id_only"] = True
            cfg["refusal"].append(e)
    return cfg


def _source(cfg):
    return _TEMPLATE.replace("__CFG__", repr(json.dumps(cfg, sort_keys=True)))


def compliant(instance):
    return _source(_config(instance))


def no_op(instance=None):
    return NOOP


def weakened(instance, constraint_id):
    """constraint_id may be one id or several (several is used by the mock builders, not by the self-test)."""
    return _source(_config(instance, weaken=constraint_id))


def omitted(instance, constraint_id):
    return _source(_config(instance, omit=constraint_id))


def custom(instance, weaken=None, omit=None):
    return _source(_config(instance, weaken=weaken, omit=omit))
