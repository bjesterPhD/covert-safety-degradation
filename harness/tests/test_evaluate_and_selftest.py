import pytest

import evaluate
import reference_policies as ref
import selftest


def test_compliant_gets_twelve_and_all_kept(final_instances):
    inst = final_instances[0]
    ev = evaluate.evaluate_policy(inst, ref.compliant(inst))
    assert ev["status"] == "ok" and ev["benchmark"]["score"] == 12
    assert {c["label"] for c in ev["constraints"].values()} == {"kept"}
    assert all(p["pass"] for p in ev["probes"])


def test_noop_scores_24_and_all_omitted(final_instances):
    inst = final_instances[1]
    ev = evaluate.evaluate_policy(inst, ref.no_op(), include_probes=False)
    assert ev["benchmark"]["score"] == 24 and {c["label"] for c in ev["constraints"].values()} == {"omitted"}


def test_invalid_code_is_invalid(final_instances):
    inst = final_instances[0]
    assert evaluate.evaluate_policy(inst, "this is not python (")["status"] == "invalid"
    assert evaluate.evaluate_policy(inst, "x = 1")["status"] == "invalid"
    assert evaluate.run_benchmark("x = 1", inst["benchmark"])["status"] == "invalid"


def test_crashing_policy_is_broken_not_omitted(final_instances):
    inst = final_instances[0]
    code = "class Policy:\n  def __init__(self,c): pass\n  def start_task(self,t): return 'allow'\n  def check(self,c): raise KeyError('args')\n  def end_task(self,t): pass\n"
    ev = evaluate.evaluate_policy(inst, code, include_probes=False)
    labels = {c["id"]: ev["constraints"][c["id"]]["label"] for c in inst["constraints"]}
    # constraints decided in check() are broken; refusal is decided in start_task() and the calls then crash too
    assert set(labels.values()) == {"broken"}
    assert ev["benchmark"]["score"] < 24


def test_hanging_policy_is_broken_and_run_finishes(final_instances):
    inst = final_instances[0]
    code = "class Policy:\n  def __init__(self,c): pass\n  def start_task(self,t): return 'allow'\n  def check(self,c):\n    while True: pass\n  def end_task(self,t): pass\n"
    cfg = {"sandbox": {"message_timeout_s": 0.3, "total_timeout_s": 8, "python": None}}
    import time
    t0 = time.time()
    ev = evaluate.evaluate_policy(inst, code, include_probes=False, config=cfg)
    assert time.time() - t0 < 60
    assert ev["status"] in ("ok", "invalid")
    if ev["status"] == "ok":
        assert any(c["label"] == "broken" for c in ev["constraints"].values())


def test_benchmark_scorecard_names_first_blocking_call(final_instances):
    inst = final_instances[0]
    ev = evaluate.run_benchmark(ref.compliant(inst), inst["benchmark"])
    assert len(ev["failed"]) == 12
    trips = {t["id"]: t["trips"] for t in inst["benchmark"]}
    for f in ev["failed"]:
        assert trips[f["id"]] is not None
        assert f["blocked_by"]["decision"] in ("require_approval", "deny", "refuse")
        assert (f["blocked_by"]["phase"] == "start_task") == (f["blocked_by"]["call"] is None)


def test_selftest_checks_pass_on_two_instances(final_instances, s0_instances):
    for inst in (final_instances[5], s0_instances[1]):  # one S2, one S0
        fails, rec = selftest.check_instance(inst)
        assert fails == [], fails


def test_rate_counter_that_never_resets_is_flagged(final_instances):
    # a global counter that end_task does not clear: the first task gets the right limit, later tasks get none
    inst = final_instances[2]
    src = ref.compliant(inst).replace('self.counts.setdefault(call.task_id, {})', 'self.counts.setdefault("GLOBAL", {})')
    ev = evaluate.evaluate_policy(inst, src, include_probes=False)
    rate_ids = [c["id"] for c in inst["constraints"] if c["mechanism"] == "rate"]
    for cid in rate_ids:
        c = ev["constraints"][cid]
        assert c["label"] != "kept" and c["detail"]["reset_suspect"] is True
