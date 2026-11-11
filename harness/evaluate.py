"""Runs a policy.py (as a string) against one instance: benchmark score, hidden-test classification, probes.

Everything executes in the sandbox (sandbox.py). No network, no LLM.
"""
from common import load_config
from sandbox import PolicyInvalid, PolicySession, SandboxFailure
from classify import classify_constraint
from benchmark import trajectory_calls
import realworld


def _session(code, config=None):
    cfg = (config or load_config())["sandbox"]
    return PolicySession(code, python=cfg.get("python"), message_timeout=cfg.get("message_timeout_s", 5),
                         total_timeout=cfg.get("total_timeout_s", 90))


# ------------------------------------------------------------------------------ benchmark

def run_benchmark_on_session(session, tasks):
    """Fresh Policy, then every task in order. A task passes iff start_task and every check return allow.
    All calls of a task are sent even after a block (trajectories are fixed), the FIRST non-allow is reported."""
    session.new_policy({})
    passed, failed = [], []
    for task in tasks:
        first_block = None

        def note(phase, decision, call=None, err=None):
            nonlocal first_block
            if first_block is None:
                first_block = {"phase": phase, "decision": decision if not err else "error: %s" % err,
                               "call": {"tool": call["tool"], "args": call["args"]} if call else None}

        try:
            r = session.start_task({"id": task["id"], "category": task["category"], "request_text": task["request_text"]})
            if r.error:
                note("start_task", None, err=r.error)
            elif r.decision != "allow":
                note("start_task", r.decision)
            for call in trajectory_calls(task):
                r = session.check(call)
                if r.error:
                    note("check", None, call, err=r.error)
                elif r.decision != "allow":
                    note("check", r.decision, call)
            r = session.end_task(task["id"])
            if r.error:
                note("end_task", None, err=r.error)
        except SandboxFailure as exc:
            note("sandbox", None, err=str(exc))
            failed.append(_failed_entry(task, first_block))
            # remaining tasks cannot run
            idx = tasks.index(task)
            for rest in tasks[idx + 1:]:
                failed.append(_failed_entry(rest, {"phase": "sandbox", "decision": "error: not run after sandbox failure", "call": None}))
            break
        if first_block is None:
            passed.append(task["id"])
        else:
            failed.append(_failed_entry(task, first_block))
    return {"status": "ok", "score": len(passed), "total": len(tasks), "passed_ids": passed, "failed": failed}


def _failed_entry(task, block):
    return {"id": task["id"], "request_text": task["request_text"], "category": task["category"],
            "blocked_by": block}


def run_benchmark(code, tasks, config=None):
    """Score a policy on the benchmark. Returns {"status":"invalid","error":...} if it cannot be loaded."""
    try:
        session = _session(code, config)
    except (PolicyInvalid, SandboxFailure) as exc:
        return {"status": "invalid", "error": str(exc)}
    try:
        return run_benchmark_on_session(session, tasks)
    except PolicyInvalid as exc:
        return {"status": "invalid", "error": str(exc)}
    except SandboxFailure as exc:
        return {"status": "invalid", "error": "sandbox: %s" % exc}
    finally:
        session.close()


# ------------------------------------------------------------------------------ cases

def run_case(session, case):
    """Raw result for one case: {id, starts, decisions, error}."""
    res = {"id": case["id"], "starts": [], "decisions": [], "error": None}

    def err(msg):
        if res["error"] is None:
            res["error"] = msg

    try:
        for t in case["tasks"]:
            r = session.start_task({"id": t["id"], "category": t["category"], "request_text": t["request_text"]})
            res["starts"].append(r.decision)
            if r.error:
                err("start_task: %s" % r.error)
        for call in case["calls"]:
            r = session.check({"task_id": call["task_id"], "tool": call["tool"], "args": call["args"]})
            res["decisions"].append(r.decision)
            if r.error:
                err("check(%s): %s" % (call["tool"], r.error))
        for t in case["tasks"]:
            r = session.end_task(t["id"])
            if r.error:
                err("end_task: %s" % r.error)
    except SandboxFailure as exc:
        err("sandbox: %s" % exc)
        while len(res["decisions"]) < len(case["calls"]):
            res["decisions"].append(None)
        while len(res["starts"]) < len(case["tasks"]):
            res["starts"].append(None)
    return res


def _all_error(cases, msg):
    return [{"id": c["id"], "starts": [None] * len(c["tasks"]), "decisions": [None] * len(c["calls"]),
             "error": "sandbox: %s" % msg} for c in cases]


def run_battery(session, cases):
    """A fresh Policy instance, then every case in order. If the sandbox cannot give us a fresh instance (for
    example the session's time budget ran out after a hang), every case counts as an error (broken), not invalid."""
    try:
        session.new_policy({})
    except (PolicyInvalid, SandboxFailure) as exc:
        return _all_error(cases, str(exc))
    return [run_case(session, c) for c in cases]


def run_probes(session, probes):
    """Probe results grouped by constraint: each probe passes iff every expectation is met."""
    results = []
    for p in probes:
        try:
            session.new_policy({})
        except (PolicyInvalid, SandboxFailure) as exc:
            results.append({"id": p["id"], "constraint": p["constraint"], "type": p["type"], "pass": False,
                            "reason": "sandbox: %s" % exc})
            continue
        raw = run_case(session, p)
        ok = raw["error"] is None
        reasons = []
        if raw["error"]:
            reasons.append(raw["error"])
        if ok:
            for call, d in zip(p["calls"], raw["decisions"]):
                if call.get("expect") is not None and d not in call["expect"]:
                    ok = False
                    reasons.append("%s -> %s (expected %s)" % (call["tool"], d, "/".join(call["expect"])))
                    break
        if ok and p.get("expect_starts"):
            for exp, d in zip(p["expect_starts"], raw["starts"]):
                if exp is not None and d not in exp:
                    ok = False
                    reasons.append("start_task -> %s (expected %s)" % (d, "/".join(exp)))
                    break
        results.append({"id": p["id"], "constraint": p["constraint"], "type": p["type"], "pass": ok,
                        "reason": "; ".join(reasons) if reasons else None})
    return results


def evaluate_policy(instance, code, include_probes=True, include_benchmark=True, config=None):
    """Full evaluation of a policy against an instance.

    Returns {"status": "ok"|"invalid", "error", "benchmark": {...}, "constraints": {cid: classification},
             "probes": [...]}
    """
    try:
        session = _session(code, config)
    except (PolicyInvalid, SandboxFailure) as exc:
        return {"status": "invalid", "error": str(exc)}
    out = {"status": "ok", "error": None}
    try:
        if include_benchmark:
            out["benchmark"] = run_benchmark_on_session(session, instance["benchmark"])
        else:
            session.new_policy({})  # a policy that cannot be instantiated is invalid, not broken
        out["constraints"] = {}
        for c in instance["constraints"]:
            hidden = instance["hidden_tests"][c["id"]]
            core = run_battery(session, hidden["core"])
            boundary = run_battery(session, hidden["boundary"])
            cls = classify_constraint(c, core, boundary)
            cls["core_raw"] = core
            cls["boundary_raw"] = boundary
            if instance.get("content_style") == "realworld":
                cls["strictness"] = realworld.strictness(c, core)   # DESIGN_REALWORLD.md 3.1
            out["constraints"][c["id"]] = cls
        if include_probes:
            out["probes"] = run_probes(session, instance["probes"])
    except PolicyInvalid as exc:
        return {"status": "invalid", "error": str(exc)}
    except SandboxFailure as exc:
        return {"status": "invalid", "error": "sandbox: %s" % exc}
    finally:
        session.close()
    return out


def summarise_labels(evaluation):
    """{constraint_id: label} or None if invalid."""
    if evaluation.get("status") != "ok":
        return None
    return {cid: c["label"] for cid, c in evaluation["constraints"].items()}
