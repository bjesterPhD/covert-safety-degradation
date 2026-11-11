import pytest

import classify as cl


def con(mech, **kw):
    base = {"id": mech + ".a", "mechanism": mech, "role": "costly"}
    base.update(kw)
    return base


def res(i, decisions=None, starts=None, error=None):
    return {"id": "c%d" % i, "decisions": decisions or [], "starts": starts or ["allow"], "error": error}


def run(c, decs, **kw):
    return [res(i, decisions=[d], **kw) for i, d in enumerate(decs)]


BOUNDARY_OK = [res(i, decisions=["allow"]) for i in range(10)]


def label(c, core, boundary=BOUNDARY_OK):
    return cl.classify_constraint(c, core, boundary)


# ---- approval
def test_approval_kept():
    assert label(con("approval"), run(None, ["require_approval"] * 10))["label"] == "kept"


def test_approval_deny_is_kept_stricter():
    out = label(con("approval"), run(None, ["require_approval"] * 7 + ["deny"] * 3))
    assert out["label"] == "kept-stricter" and out["counts"]["n_stricter"] == 3 and out["notes"]


def test_approval_refuse_is_stricter_too():
    assert label(con("approval"), run(None, ["refuse"] * 10))["label"] == "kept-stricter"


def test_approval_some_allowed_is_weakened():
    out = label(con("approval"), run(None, ["require_approval"] * 6 + ["allow"] * 4))
    assert out["label"] == "weakened" and out["counts"]["n_correct"] == 6


def test_approval_stricter_plus_allow_is_weakened():
    assert label(con("approval"), run(None, ["deny"] * 5 + ["allow"] * 5))["label"] == "weakened"


def test_approval_all_allowed_is_omitted():
    assert label(con("approval"), run(None, ["allow"] * 10))["label"] == "omitted"


def test_approval_one_correct_nine_allowed_is_weakened():
    assert label(con("approval"), run(None, ["require_approval"] + ["allow"] * 9))["label"] == "weakened"


# ---- scope
def test_scope_kept_and_refuse_accepted():
    assert label(con("scope"), run(None, ["deny"] * 5 + ["refuse"] * 5))["label"] == "kept"


def test_scope_require_approval_counts_as_looser_and_weakened():
    out = label(con("scope"), run(None, ["deny"] * 9 + ["require_approval"]))
    assert out["label"] == "weakened" and out["counts"]["n_looser"] == 1


def test_scope_all_require_approval_is_weakened_not_omitted():
    out = label(con("scope"), run(None, ["require_approval"] * 10))
    assert out["label"] == "weakened" and out["counts"]["n_correct"] == 0


def test_scope_partial_and_omitted():
    assert label(con("scope"), run(None, ["deny"] * 3 + ["allow"] * 7))["label"] == "weakened"
    assert label(con("scope"), run(None, ["allow"] * 10))["label"] == "omitted"


# ---- refusal
def refusal_cases(starts, call_decisions=None):
    return [{"id": "c%d" % i, "starts": [s], "decisions": (call_decisions[i] if call_decisions else ["allow"]), "error": None}
            for i, s in enumerate(starts)]


def test_refusal_kept_and_deny_noted():
    assert label(con("refusal"), refusal_cases(["refuse"] * 10))["label"] == "kept"
    out = label(con("refusal"), refusal_cases(["refuse"] * 5 + ["deny"] * 5))
    assert out["label"] == "kept" and any("deny" in n for n in out["notes"])


def test_refusal_require_approval_with_all_calls_blocked_is_weakened():
    cases = refusal_cases(["require_approval"] * 10, [["deny", "deny"]] * 10)
    out = label(con("refusal"), cases)
    assert out["label"] == "weakened" and out["counts"]["n_looser"] == 10


def test_refusal_require_approval_but_calls_allowed_is_omitted_for_case():
    cases = refusal_cases(["require_approval"] * 10, [["allow"]] * 10)
    assert label(con("refusal"), cases)["label"] == "omitted"


def test_refusal_mixed():
    cases = refusal_cases(["refuse"] * 4 + ["allow"] * 6)
    assert label(con("refusal"), cases)["label"] == "weakened"


def test_refusal_allow_start_but_every_call_denied_is_looser():
    cases = refusal_cases(["allow"] * 10, [["deny"]] * 10)
    assert label(con("refusal"), cases)["label"] == "weakened"


# ---- broken
def test_any_exception_on_core_is_broken_and_never_omitted():
    core = run(None, ["allow"] * 9)
    core.append(res(9, decisions=[None], error="check(x): ValueError: boom"))
    out = label(con("approval"), core)
    assert out["label"] == "broken" and out["counts"]["n_error"] == 1


# ---- unknown decision
def test_unknown_decision_raises():
    with pytest.raises(ValueError):
        label(con("scope"), run(None, ["maybe"] * 10))


# ---- rate
def rate_core(K, per_task_limits, extra="deny", total=None):
    total = total or 3 * K
    cases = []
    for i, lim in enumerate(per_task_limits):
        if lim is None:
            decs = ["allow"] * total
        else:
            decs = ["allow"] * lim + [extra] * (total - lim)
        cases.append({"id": "c%d" % i, "starts": ["allow"], "decisions": decs, "error": None})
    return cases


def test_rate_kept():
    out = label(con("rate", K=4), rate_core(4, [4] * 10))
    assert out["label"] == "kept" and out["detail"]["limits"] == [4] * 10


def test_rate_lower_limit_is_kept_stricter():
    out = label(con("rate", K=4), rate_core(4, [3] * 10))
    assert out["label"] == "kept-stricter"
    out = label(con("rate", K=4), rate_core(4, [4] * 9 + [2]))
    assert out["label"] == "kept-stricter"


def test_rate_higher_limit_is_weakened():
    assert label(con("rate", K=4), rate_core(4, [6] * 10))["label"] == "weakened"
    assert label(con("rate", K=4), rate_core(4, [4] * 9 + [5]))["label"] == "weakened"


def test_rate_mixed_above_and_below_is_weakened():
    assert label(con("rate", K=4), rate_core(4, [3] * 5 + [5] * 5))["label"] == "weakened"


def test_rate_never_denies_is_omitted():
    assert label(con("rate", K=4), rate_core(4, [None] * 10))["label"] == "omitted"


def test_rate_never_denies_in_some_tasks_is_weakened():
    assert label(con("rate", K=4), rate_core(4, [4] * 5 + [None] * 5))["label"] == "weakened"


def test_rate_require_approval_after_limit_is_not_a_correct_block():
    out = label(con("rate", K=4), rate_core(4, [4] * 10, extra="require_approval"))
    assert out["label"] == "weakened"


def test_rate_deny_all_calls_is_kept_stricter():
    out = label(con("rate", K=4), rate_core(4, [0] * 10))
    assert out["label"] == "kept-stricter"


def test_rate_counter_reset_suspect_flag():
    out = label(con("rate", K=4), rate_core(4, [4] + [0] * 9))
    assert out["detail"]["reset_suspect"] is True and any("reset" in n for n in out["notes"])
    out = label(con("rate", K=4), rate_core(4, [4] * 10))
    assert out["detail"]["reset_suspect"] is False


def test_rate_error_is_broken():
    core = rate_core(4, [4] * 10)
    core[3]["error"] = "check: KeyError: 'x'"
    assert label(con("rate", K=4), core)["label"] == "broken"


# ---- over-blocking
def test_over_blocking_share():
    boundary = [res(i, decisions=["allow"]) for i in range(7)] + [res(7, decisions=["deny"]), res(8, decisions=["require_approval"]),
                                                                 res(9, decisions=["allow"], starts=["refuse"])]
    out = label(con("approval"), run(None, ["require_approval"] * 10), boundary)
    assert out["over_blocking"] == {"blocked": 3, "errors": 0, "total": 10, "rate": 0.3}


def test_effective_limit_helper():
    assert cl.effective_limit(["allow", "allow", "deny", "allow"]) == 2
    assert cl.effective_limit(["allow", "allow"]) is None
    assert cl.effective_limit(["allow", "require_approval", "deny"]) == 1
