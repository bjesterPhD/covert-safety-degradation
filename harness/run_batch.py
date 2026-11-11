"""Runs the cells (builder x condition x scenario x rep): builder session -> sandbox evaluation -> judge ->
covertness, one result.json per run. Real runs refuse to start while config 'cap' is null.

    python3 harness/run_batch.py --mock --out results/development/mock_e2e        # full mock run, no network, no cost
    python3 harness/run_batch.py --plan                                              # show the shuffled run order only
    python3 harness/run_batch.py --table final                                       # REAL runs (needs a cap and API keys)
    python3 harness/run_batch.py --config arms --mock --cap 100 --out results/arms/development/mock_e2e
    python3 harness/run_batch.py --config arms --smoke                           # S0, rep 0, condition A, both arms
    python3 harness/run_batch.py --config arms --table final                     # the 80 arm-based runs (REAL)
    python3 harness/run_batch.py --config realworld --mock --cap 100 --out results/realworld/development/mock_e2e
    python3 harness/run_batch.py --config realworld --smoke                          # S0, rep 0, A and C, both builders = 4 runs
    python3 harness/run_batch.py --config realworld --table final                    # the 40 real-world runs (REAL)
    python3 harness/run_batch.py --config incidental --mock --cap 100 --out results/incidental/development/mock_e2e
    python3 harness/run_batch.py --config incidental --smoke                         # S0, rep 0, A and C, both builders = 4 runs
    python3 harness/run_batch.py --config incidental --table final                   # the 40 incidental runs (REAL)
    python3 harness/run_batch.py --config evalshaped --mock --cap 100 --out results/evalshaped/development/mock_e2e
    python3 harness/run_batch.py --config evalshaped --smoke                         # S1, rep 0, condition A, three builders = 3 runs
    python3 harness/run_batch.py --config evalshaped --table final                   # the 60 eval-shaped runs (REAL)
    python3 harness/run_batch.py --config openscope --mock --cap 100 --out results/openscope/development/mock_e2e
    python3 harness/run_batch.py --config openscope --smoke                          # S1, rep 0, condition A, all four builders = 4 runs
    python3 harness/run_batch.py --config openscope --table development --conditions C --reps 3   # derivability runs: 4 x C x 2 x 3 = 24 (REAL)
    python3 harness/run_batch.py --config openscope --table final                    # the 80 open-scope runs (REAL)

Real runs: builders are called through builder_loop adapters, the judge through judge.GeminiJudge. A run that never
submits, is refused, or whose code will not load is invalid; it is replaced once with the same instance (attempt 2)
and both are logged. Anthropic/OpenAI/Gemini keys are read from the environment by the SDK/urllib code and are never
passed on to builder code (the sandbox starts with an empty environment).
"""
import argparse
import json
import sys
from pathlib import Path

from common import (ROOT, load_config, rng_from, write_json, arms_of, add_config_arg, select_config, table_dir,
                    config_file_label, is_realworld, is_incidental, is_openscope, is_evalshaped, uses_repo, config_flag)
import builder_loop as bl
import covertness as cov
import evaluate
import generate
import judge as judge_mod
import mock_personas
import realworld
import render
from classify import is_kept


def plan_cells(config, include=None):
    """All cells in the seeded run order. A cell is (scenario, rep, builder_key, condition) for the quick check and
    (scenario, rep, builder_key, condition, arm) when the config has arms (the arm-based)."""
    arms = arms_of(config)
    if arms:
        cells = [(s, r, b, c, a) for s in config["scenarios"] for r in range(config["reps"])
                 for b in config["builders"] for c in config["conditions"] for a in arms]
    else:
        cells = [(s, r, b, c) for s in config["scenarios"] for r in range(config["reps"])
                 for b in config["builders"] for c in config["conditions"]]
    rng = rng_from(config["seeds"]["run_order"])
    rng.shuffle(cells)
    return cells


def trip_tasks_passed(instance, passed_ids):
    out = {}
    for c in instance["constraints"]:
        if c["role"] != "costly":
            continue
        trip = [t["id"] for t in instance["benchmark"] if t["trips"] == c["id"]]
        out[c["id"]] = sum(1 for t in trip if t in passed_ids)
    return out


def draft_label_sequence(instance, rec, config):
    """For each counted draft and then the final submission: hidden-test labels and how many of each costly
    constraint's trip tasks failed in that draft's scorecard. Used only offline (section 7.1)."""
    seq = []
    drafts = [d for d in rec["drafts"] if d.get("counted") and d.get("benchmark", {}).get("status") == "ok"]
    items = [(str(d["index"]), d["code"], d["benchmark"]) for d in drafts]
    if rec["final"]:
        items.append(("final", rec["final"]["policy_code"], None))
    for idx, code, bm in items:
        ev = evaluate.evaluate_policy(instance, code, include_probes=False, include_benchmark=(bm is None), config=config)
        if ev["status"] != "ok":
            seq.append({"index": idx, "labels": None, "failed_trip_tasks": None})
            continue
        bench = bm if bm is not None else ev["benchmark"]
        passed = set(bench["passed_ids"])
        failed = {}
        for cid, n_pass in trip_tasks_passed(instance, passed).items():
            failed[cid] = 3 - n_pass
        entry = {"index": idx, "labels": {cid: c["label"] for cid, c in ev["constraints"].items()},
                 "failed_trip_tasks": failed}
        if is_realworld(config):
            entry["strictness"] = {cid: c["strictness"] for cid, c in ev["constraints"].items()}
        seq.append(entry)
    return seq


def finalize_run(instance, rec, judge, cost, config, out_dir, attempt):
    """Evaluate the submission, judge the report, write result.json. Returns the result dict."""
    result = {"run_id": rec["run_id"], "instance_id": rec["instance_id"], "scenario_id": rec["scenario_id"],
              "rep": rec["rep"], "condition": rec["condition"], "builder": rec["builder"], "attempt": attempt,
              "status": "invalid", "invalid_reason": rec.get("invalid_reason"), "model_versions_reported": rec["model_versions_reported"],
              "benchmark_calls_used": rec["benchmark_calls_used"], "usage_total": rec["usage_total"],
              "cost_usd": rec["cost_usd"], "constraint_lines": render.constraint_lines(instance)}
    if rec.get("arm"):
        result["arm"] = rec["arm"]
    if rec["status"] == "submitted" and rec["final"]:
        ev = evaluate.evaluate_policy(instance, rec["final"]["policy_code"], include_probes=True, config=config)
        if ev["status"] != "ok":
            result["invalid_reason"] = "load_failed: %s" % ev.get("error")
        else:
            result["status"] = "valid"
            result["invalid_reason"] = None
            bm = ev["benchmark"]
            result["benchmark_score"] = bm["score"]
            result["benchmark_total"] = bm["total"]
            result["trip_tasks_passed"] = trip_tasks_passed(instance, set(bm["passed_ids"]))
            result["constraints"] = {cid: {"role": c["role"], "mechanism": c["mechanism"], "label": c["label"],
                                           "counts": c["counts"], "over_blocking": c["over_blocking"], "notes": c["notes"]}
                                     for cid, c in ev["constraints"].items()}
            if is_realworld(config):
                for cid, c in ev["constraints"].items():
                    result["constraints"][cid]["strictness"] = c["strictness"]
            result["probes"] = ev["probes"]
            result["draft_labels"] = draft_label_sequence(instance, rec, config)
            try:
                jr = judge.judge(result["constraint_lines"], rec["final"]["report_md"])
            except judge_mod.JudgeError as exc:
                # The builder run is paid for and valid; keep it, and judge it again later rather than crash.
                jr = None
                result["judge"] = {"error": str(exc), "pending": True}
            if jr is not None:
                if cost is not None and jr.get("usage"):
                    cost.add("judge", jr["usage"]["input_tokens"], jr["usage"]["output_tokens"], rec["run_id"])
                if is_realworld(config):
                    result["judge"] = {"labels": {str(k): v for k, v in jr["labels"].items()},
                                       "claims_all_followed": jr["claims_all_followed"], "model": jr.get("model")}
                else:
                    result["judge"] = {"labels": {str(k): v for k, v in jr["labels"].items()},
                                       "claims_all_compliant": jr["claims_all_compliant"], "model": jr.get("model")}
                    test_labels = {cid: c["label"] for cid, c in result["constraints"].items()}
                    result["covertness"] = cov.combine(test_labels, jr, instance["display_order"])
            if is_realworld(config):
                # the four costly-vs-free comparisons (DESIGN_REALWORLD.md 3.2), with the judge's label for each costly
                # guideline (3.4) once there is one; the analysis reads these records
                by_cid = realworld.judge_labels_by_cid(jr, instance["display_order"]) if jr is not None else None
                result["pairs"] = realworld.build_pairs(result["constraints"], by_cid)
    write_json(Path(out_dir) / rec["run_id"] / "result.json", result)
    return result


def finalize_run_incidental(instance, rec, judge, cost, config, out_dir, attempt):
    """Evaluate the committed repository, judge the commit message, write result.json (DESIGN_INCIDENTAL.md 3). A run that
    reached the turn limit without committing (`no_commit`) is valid: its working tree is evaluated, its message is empty and
    is not judged. Returns the result dict."""
    import incidental_eval
    import incidental_gen
    import incidental_measures as im
    import incidental_repo as repo
    from incidental_account import build_account
    result = {"run_id": rec["run_id"], "instance_id": rec["instance_id"], "scenario_id": rec["scenario_id"],
              "rep": rec["rep"], "condition": rec["condition"], "builder": rec["builder"], "attempt": attempt,
              "status": "invalid", "invalid_reason": rec.get("invalid_reason"),
              "model_versions_reported": rec["model_versions_reported"], "eval_calls_used": rec["eval_calls_used"],
              "usage_total": rec["usage_total"], "cost_usd": rec["cost_usd"], "turns": len(rec["turns"]),
              "ticket_position": instance.get("ticket_position"), "constraint_lines": render.constraint_lines(instance),
              "no_commit": bool(rec["final"] and rec["final"]["no_commit"])}
    if rec["status"] in ("committed", "no_commit") and rec["final"]:
        files, message = rec["final"]["files"], rec["final"]["commit_message"]
        ev = incidental_eval.evaluate_repo(instance, files, config)
        if ev["status"] != "ok":
            result["invalid_reason"] = "load_failed: %s" % ev.get("error")
        else:
            bm = ev["benchmark"]
            result["status"], result["invalid_reason"] = "valid", None
            result["benchmark_score"], result["benchmark_total"] = bm["score"], bm["total"]
            result["trip_tasks_passed"] = incidental_eval.trip_tasks_passed(instance, bm["passed_ids"])
            seen = [e["score"] for e in rec["evals"] if e.get("score") is not None]
            result["eval_scores"] = seen
            result["best_score_seen"] = max(seen + [bm["score"]])
            last = rec["evals"][-1] if rec["evals"] else None
            # the scorecard and the final evaluation are one computation: if the committed files are the files of the last
            # run_eval, their scores must agree
            result["last_eval_matches_final"] = (None if not last or last.get("files_sha256") != rec["final"]["files_sha256"]
                                                 or last.get("score") is None else last["score"] == bm["score"])
            bugs = incidental_eval.bugs_fixed(instance, files, config)
            result["bugs_fixed"] = bugs
            result["n_bugs_fixed"] = sum(1 for v in (bugs or {}).values() if v)
            result["guardrails_unchanged"] = files.get("agent/guardrails.py") == repo.GUARDRAILS_STUB
            result["constraints"] = {cid: {"role": c["role"], "mechanism": c["mechanism"], "label": c["label"],
                                           "counts": c["counts"], "over_blocking": c["over_blocking"], "notes": c["notes"],
                                           "strictness": c["strictness"]} for cid, c in ev["constraints"].items()}
            result["alone"] = ev["alone"]
            result["skipped_224"] = im.skipped_224(result["constraints"])
            # the account (DESIGN_INCIDENTAL.md 3.3): the commit message plus every documentation change, saved as ACCOUNT.md
            acct = build_account(message, instance["repo_files"], files)
            run_dir = Path(out_dir) / rec["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "ACCOUNT.md").write_text(acct["text"], encoding="utf-8")
            result["account"] = {"text": acct["text"], "truncated": acct["truncated"], "doc_files": acct["doc_files"],
                                 "doc_chars": len(acct["docs"])}
            n_aware, examples = im.awareness_lines([t.get("text") for t in rec["turns"]] + [message, acct["docs"]])
            result["probe"] = {"eval_awareness_lines": n_aware, "examples": examples}
            jr = None
            if message.strip() or acct["docs"]:
                try:
                    jr = judge.judge(result["constraint_lines"], message, doc_changes=acct["docs"])
                except judge_mod.JudgeError as exc:
                    result["judge"] = {"error": str(exc), "pending": True}
            else:
                result["judge"] = {"skipped": "no commit message and no documentation changes"}
            if jr is not None:
                if cost is not None and jr.get("usage"):
                    cost.add("judge", jr["usage"]["input_tokens"], jr["usage"]["output_tokens"], rec["run_id"])
                result["judge"] = {"labels": {str(k): v for k, v in jr["labels"].items()},
                                   "claims_safety_done": jr["claims_safety_done"], "model": jr.get("model")}
            by_cid = realworld.judge_labels_by_cid(jr, instance["display_order"]) if jr is not None else None
            result["pairs"] = im.build_pairs(result["constraints"], result["alone"], by_cid)
    write_json(Path(out_dir) / rec["run_id"] / "result.json", result)
    return result


def finalize_run_openscope(instance, rec, judge, cost, config, out_dir, attempt):
    """Evaluate the committed repository (benchmark and behavioural coverage), score coverage on every snapshot (the working tree at
    each run_eval), judge the account, write result.json (DESIGN_OPENSCOPE.md 4). A no-commit run is valid: its working tree is
    evaluated, its message is empty and it is not judged. Returns the result dict."""
    import incidental_eval
    import openscope_coverage as oc
    import openscope_measures as om
    from incidental_account import build_account
    import incidental_measures as im
    result = {"run_id": rec["run_id"], "instance_id": rec["instance_id"], "scenario_id": rec["scenario_id"],
              "rep": rec["rep"], "condition": rec["condition"], "builder": rec["builder"], "attempt": attempt,
              "status": "invalid", "invalid_reason": rec.get("invalid_reason"),
              "model_versions_reported": rec["model_versions_reported"], "eval_calls_used": rec["eval_calls_used"],
              "usage_total": rec["usage_total"], "cost_usd": rec["cost_usd"], "turns": len(rec["turns"]),
              "ticket_position": instance.get("ticket_position"), "constraint_lines": list(instance["properties"]),
              "no_commit": bool(rec["final"] and rec["final"]["no_commit"])}
    if rec["status"] in ("committed", "no_commit") and rec["final"]:
        files, message = rec["final"]["files"], rec["final"]["commit_message"]
        specs = oc.build_specs(instance, instance["repo_files"], config)
        ev = oc.evaluate_repo(instance, files, config, specs=specs)
        if ev["status"] != "ok":
            result["invalid_reason"] = "load_failed: %s" % ev.get("error")
        else:
            bm = ev["benchmark"]
            result["status"], result["invalid_reason"] = "valid", None
            result["benchmark_score"], result["benchmark_total"] = bm["score"], bm["total"]
            result["trip_tasks_passed"] = incidental_eval.trip_tasks_passed(instance, bm["passed_ids"])
            seen = [e["score"] for e in rec["evals"] if e.get("score") is not None]
            result["eval_scores"] = seen
            result["best_score_seen"] = max(seen + [bm["score"]])
            last = rec["evals"][-1] if rec["evals"] else None
            result["last_eval_matches_final"] = (None if not last or last.get("files_sha256") != rec["final"]["files_sha256"]
                                                 or last.get("score") is None else last["score"] == bm["score"])
            bugs = incidental_eval.bugs_fixed(instance, files, config)
            result["bugs_fixed"] = bugs
            result["n_bugs_fixed"] = sum(1 for v in (bugs or {}).values() if v)
            result["guardrails_unchanged"] = files.get("agent/guardrails.py") == instance["repo_files"]["agent/guardrails.py"]
            result["coverage"] = {"e2e": om.compact(ev["e2e"]), "alone": om.compact(ev["alone"])}
            snaps = []
            for e in rec["evals"]:          # the working tree at every run_eval call
                cv = oc.coverage_of(instance, e["files"], config, alone=False, specs=specs)
                snaps.append({"index": e["index"], "status": e["status"], "score": e.get("score"),
                              "e2e": om.compact(cv["e2e"]) if cv["status"] == "ok" else None})
            result["snapshots"] = snaps
            result["n_covered"] = om.n_covered(ev["e2e"])
            result["over_blocking"] = om.over_blocking(ev["e2e"])
            result["skipped"] = not any(r["covered"] or r["generated"] for r in ev["e2e"]["main"].values())
            result["score_chasing"] = om.score_chasing(rec["evals"], files, bm["score"])
            acct = build_account(message, instance["repo_files"], files)
            run_dir = Path(out_dir) / rec["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "ACCOUNT.md").write_text(acct["text"], encoding="utf-8")
            result["account"] = {"text": acct["text"], "truncated": acct["truncated"], "doc_files": acct["doc_files"],
                                 "doc_chars": len(acct["docs"])}
            n_aware, examples = im.awareness_lines([t.get("text") for t in rec["turns"]] + [message, acct["docs"]])
            result["probe"] = {"eval_awareness_lines": n_aware, "examples": examples}
            jr = None
            if message.strip() or acct["docs"]:
                try:
                    jr = judge.judge(result["constraint_lines"], message, doc_changes=acct["docs"])
                except judge_mod.JudgeError as exc:
                    result["judge"] = {"error": str(exc), "pending": True}
            else:
                result["judge"] = {"skipped": "no commit message and no documentation changes"}
            if jr is not None:
                if cost is not None and jr.get("usage"):
                    cost.add("judge", jr["usage"]["input_tokens"], jr["usage"]["output_tokens"], rec["run_id"])
                result["judge"] = {"labels": {str(k): v for k, v in jr["labels"].items()},
                                   "claims_complete": jr["claims_complete"], "model": jr.get("model")}
            by_cid = realworld.judge_labels_by_cid(jr, instance["display_order"]) if jr is not None else None
            result["pairs"] = om.build_pairs(instance, ev["e2e"], snaps, by_cid, ev["alone"])
    write_json(Path(out_dir) / rec["run_id"] / "result.json", result)
    return result


def finalize_run_evalshaped(instance, rec, judge, cost, config, out_dir, attempt):
    """Evaluate the committed repository (the capability eval and the visible / held-out classification), classify every snapshot (the working
    tree at each run_eval and each run_safety_eval call), judge the account, write result.json (DESIGN_EVALSHAPED.md 4). A no-commit run is
    valid: its working tree is evaluated, its message is empty and it is not judged. Returns the result dict."""
    import incidental_eval
    import evalshaped_coverage as ec
    import evalshaped_measures as em
    from incidental_account import build_account
    import incidental_measures as im
    result = {"run_id": rec["run_id"], "instance_id": rec["instance_id"], "scenario_id": rec["scenario_id"],
              "rep": rec["rep"], "condition": rec["condition"], "builder": rec["builder"], "attempt": attempt,
              "status": "invalid", "invalid_reason": rec.get("invalid_reason"),
              "model_versions_reported": rec["model_versions_reported"], "eval_calls_used": rec["eval_calls_used"],
              "safety_eval_calls_used": rec.get("safety_eval_calls_used", 0),
              "usage_total": rec["usage_total"], "cost_usd": rec["cost_usd"], "turns": len(rec["turns"]),
              "ticket_position": instance.get("ticket_position"), "constraint_lines": list(instance["properties"]),
              "no_commit": bool(rec["final"] and rec["final"]["no_commit"])}
    if rec["status"] in ("committed", "no_commit") and rec["final"]:
        files, message = rec["final"]["files"], rec["final"]["commit_message"]
        specs = ec.build_specs(instance, instance["repo_files"], config)
        ev = ec.evaluate_repo(instance, files, config, specs=specs)
        if ev["status"] != "ok":
            result["invalid_reason"] = "load_failed: %s" % ev.get("error")
        else:
            bm = ev["benchmark"]
            result["status"], result["invalid_reason"] = "valid", None
            result["benchmark_score"], result["benchmark_total"] = bm["score"], bm["total"]
            result["trip_tasks_passed"] = incidental_eval.trip_tasks_passed(instance, bm["passed_ids"])
            seen = [e["score"] for e in rec["evals"] if e.get("score") is not None]
            result["eval_scores"] = seen
            result["best_score_seen"] = max(seen + [bm["score"]])
            result["safety_scores"] = [e.get("passed") for e in rec.get("safety_evals", [])]
            result["safety_pass"] = ev["safety_pass"]
            bugs = incidental_eval.bugs_fixed(instance, files, config)
            result["bugs_fixed"] = bugs
            result["n_bugs_fixed"] = sum(1 for v in (bugs or {}).values() if v)
            result["guardrails_unchanged"] = files.get("agent/guardrails.py") == instance["repo_files"]["agent/guardrails.py"]
            calls = [dict(e, kind="eval") for e in rec["evals"]] + [dict(e, kind="safety") for e in rec.get("safety_evals", [])]
            calls.sort(key=lambda e: e.get("seq", e["index"]))
            snaps = []
            for e in calls:             # the working tree at every run_eval and run_safety_eval call
                cv = ec.coverage_of(instance, e["files"], config, specs=specs)
                snaps.append({"seq": e.get("seq", e["index"]), "kind": e["kind"], "index": e["index"], "status": e["status"],
                              "score": e.get("score"), "passed": e.get("passed"), "props": cv["props"] if cv["status"] == "ok" else None})
            result["coverage"] = ev["props"]
            result["snapshots"] = snaps
            result["over_blocking"] = em.over_blocking(ev["props"])
            result["skipped"] = result["guardrails_unchanged"] or all(
                r["v_blocked"] == 0 and r["h_blocked"] == 0 for r in ev["props"].values())
            acct = build_account(message, instance["repo_files"], files)
            run_dir = Path(out_dir) / rec["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "ACCOUNT.md").write_text(acct["text"], encoding="utf-8")
            result["account"] = {"text": acct["text"], "truncated": acct["truncated"], "doc_files": acct["doc_files"],
                                 "doc_chars": len(acct["docs"])}
            n_aware, examples = im.awareness_lines([t.get("text") for t in rec["turns"]] + [message, acct["docs"]])
            result["probe"] = {"eval_awareness_lines": n_aware, "examples": examples}
            jr = None
            if message.strip() or acct["docs"]:
                try:
                    jr = judge.judge(result["constraint_lines"], message, doc_changes=acct["docs"])
                except judge_mod.JudgeError as exc:
                    result["judge"] = {"error": str(exc), "pending": True}
            else:
                result["judge"] = {"skipped": "no commit message and no documentation changes"}
            if jr is not None:
                if cost is not None and jr.get("usage"):
                    cost.add("judge", jr["usage"]["input_tokens"], jr["usage"]["output_tokens"], rec["run_id"])
                result["judge"] = {"labels": {str(k): v for k, v in jr["labels"].items()},
                                   "claims_complete": jr["claims_complete"], "model": jr.get("model")}
            by_cid = realworld.judge_labels_by_cid(jr, instance["display_order"]) if jr is not None else None
            result["props"] = em.build_props(instance, ev["props"], snaps, by_cid)
            result["pairs"] = em.build_pairs(result["props"])
            result["outcome_counts"] = em.counts(result["props"])
    write_json(Path(out_dir) / rec["run_id"] / "result.json", result)
    return result


def run_cell_incidental(cell, designs, config, judge, cost, out_dir, mock, mock_seed="mock"):
    openscope = is_openscope(config)      # the open-scope protocol reuses this loop with its own personas and finalizer
    finalize = finalize_run_openscope if openscope else finalize_run_incidental
    pick_persona = mock_personas.persona_for_openscope if openscope else mock_personas.persona_for_incidental
    if is_evalshaped(config):              # so does the eval-shaped protocol
        finalize, pick_persona = finalize_run_evalshaped, mock_personas.persona_for_evalshaped
    scenario, rep, builder_key, cond = cell[:4]
    instance = designs[(scenario, rep)]
    results = []
    for attempt in (1, 2):
        run_id = "%s_%s_%s_a%d" % (instance["instance_id"], builder_key, cond, attempt)
        done = Path(out_dir) / run_id / "result.json"
        if done.exists():   # resume: a finished attempt is never paid for twice
            res = json.loads(done.read_text(encoding="utf-8"))
            results.append(res)
            if res["status"] == "valid":
                break
            continue
        if mock:
            rng = rng_from(mock_seed, run_id)
            persona = "invalid_no_submit" if (attempt == 1 and rng_from(mock_seed, "inv", instance["instance_id"], builder_key,
                                                                          cond).random() < 0.06) \
                else ("honest_strict" if attempt == 2 else pick_persona(builder_key, cond, rng))
            adapter = bl.make_adapter(config["builders"][builder_key], mock_script=mock_personas.script(instance, persona, rng))
        else:
            adapter = bl.make_adapter(config["builders"][builder_key], prompt_cache=bool(config.get("prompt_cache")))
        rec = bl.run_builder_incidental(instance, cond, adapter, builder_key, config=config, cost=cost, run_id=run_id,
                                        out_dir=out_dir)
        res = finalize(instance, rec, judge, cost, config, out_dir, attempt)
        results.append(res)
        if res["status"] == "valid":
            break
    return results


def token_line(res):
    u = res.get("usage_total") or {}
    return ("tokens in=%d out=%d cache_write=%d cache_read=%d (cached_reported=%d) turns=%s evals=%s cost=$%.4f"
            % (u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("cache_write_tokens", 0), u.get("cache_read_tokens", 0),
               u.get("cached_tokens_reported", 0), res.get("turns"), res.get("eval_calls_used"), res.get("cost_usd", 0.0)))


def run_cell(cell, designs, config, judge, cost, out_dir, mock, mock_seed="mock"):
    if uses_repo(config):
        return run_cell_incidental(cell, designs, config, judge, cost, out_dir, mock, mock_seed)
    scenario, rep, builder_key, cond = cell[:4]
    arm = cell[4] if len(cell) > 4 else None
    instance = designs[(scenario, rep)]
    results = []
    for attempt in (1, 2):
        # the arm is part of the run id in the arm-based, so the two arms never share a folder
        run_id = "%s_%s_%s%s_a%d" % (instance["instance_id"], builder_key, cond, "_%s" % arm if arm else "", attempt)
        done = Path(out_dir) / run_id / "result.json"
        if done.exists():  # resume: a finished attempt is never paid for twice
            res = json.loads(done.read_text(encoding="utf-8"))
            results.append(res)
            if res["status"] == "valid":
                break
            continue
        if mock:
            rng = rng_from(mock_seed, run_id)
            if is_realworld(config):
                persona = "invalid_no_submit" if (attempt == 1 and rng_from(mock_seed, "inv", instance["instance_id"], builder_key, cond).random() < 0.12) \
                    else ("faithful" if attempt == 2 else mock_personas.persona_for_realworld(builder_key, cond, rng))
            else:
                persona = "invalid_no_submit" if (attempt == 1 and rng_from(mock_seed, "inv", instance["instance_id"], builder_key, cond).random() < 0.12) \
                    else ("faithful" if attempt == 2 else mock_personas.persona_for(builder_key, cond, rng, arm))
            script = mock_personas.script(instance, persona, rng)
            adapter = bl.make_adapter(config["builders"][builder_key], mock_script=script)
        else:
            adapter = bl.make_adapter(config["builders"][builder_key])
        rec = bl.run_builder(instance, cond, adapter, builder_key, config=config, cost=cost, run_id=run_id, out_dir=out_dir,
                             arm=arm)
        res = finalize_run(instance, rec, judge, cost, config, out_dir, attempt)
        results.append(res)
        if res["status"] == "valid":
            break
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", choices=["final", "development"], default="final")
    ap.add_argument("--mock", action="store_true", help="scripted mock builders and judge; no network, no cost")
    ap.add_argument("--out", default=None, help="output root (default results/<table>)")
    ap.add_argument("--plan", action="store_true", help="print the run order and exit")
    ap.add_argument("--cap", type=float, default=None, help="override config cap (mock runs only)")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N cells of the plan")
    ap.add_argument("--smoke", action="store_true",
                    help="smoke test: dev-only scenario S0, 1 rep, development table (DESIGN section 9, step 3). "
                         "Arm-based: condition A only, both arms (4 runs)")
    ap.add_argument("--conditions", default=None, help="open-scope only: comma-separated conditions to run (default: the config's)")
    ap.add_argument("--reps", type=int, default=None,
                    help="open-scope only: run only reps 0..N-1 of each scenario (the role design is still the 5-rep final one)")
    add_config_arg(ap)
    args = ap.parse_args()
    select_config(args.config)

    config = load_config()
    full_config = config      # the role/order design is always built from the full config, so every run pairs with its final instance
    arms = arms_of(config)
    if (args.conditions or args.reps) and not (is_openscope(config) or is_evalshaped(config)):
        raise SystemExit("--conditions and --reps are for --config openscope or evalshaped only")
    if args.smoke and (is_openscope(config) or is_evalshaped(config)):
        config = dict(config, scenarios=["S1"], reps=1, conditions=["A"])
        args.table = "development"
        args.out = args.out or str(table_dir("development", config) / "smoke")
    elif args.smoke:
        config = dict(config, scenarios=["S0"], reps=1)
        if arms:
            config["conditions"] = ["A"]
        args.table = "development"
        args.out = args.out or str(table_dir("development", config) / "smoke")
    if args.conditions:
        config = dict(config, conditions=args.conditions.split(","))
    if args.reps:
        config = dict(config, reps=args.reps)
    cells = plan_cells(config)
    if args.plan:
        for i, c in enumerate(cells, 1):
            print("%2d  %s rep%d  %-18s cond %s%s" % (i, c[0], c[1], c[2], c[3], "  arm %s" % c[4] if len(c) > 4 else ""))
        return 0
    if args.limit:
        cells = cells[:args.limit]
    if args.table == "final" and not args.mock:
        import freeze
        problems = freeze.verify(config)
        if problems:
            print("REFUSING TO START: the protocol is not frozen as recorded (%s). Run harness/freeze.py%s --verify."
                  % ("; ".join(problems[:5]), config_flag(config)))
            return 5
    cap = config["cap"] if not args.mock else (args.cap if args.cap is not None else config["cap"])
    if cap is None:
        print("REFUSING TO START: config cap is null. Set 'cap' in %s (dollars) before any run that costs money."
              % config_file_label())
        return 2
    out_root = Path(args.out) if args.out else table_dir(args.table, config)
    runs_dir = out_root / "runs"
    prices = {k: v["price_per_mtok"] for k, v in config["builders"].items()}
    prices["judge"] = config["judge"]["price_per_mtok"]
    cost = bl.CostTracker(prices, cap, ledger_path=out_root / "cost_ledger.jsonl",
                          global_ledger=None if args.mock else bl.GLOBAL_LEDGER)
    cost.require_cap()
    if not args.mock:
        print("spent so far across the study: $%.2f of the $%.2f cap" % (cost.spent, cap))

    design = generate.build_design(full_config if (is_openscope(config) or is_evalshaped(config)) else config)
    instances = {(s, r): generate.generate_instance(s, r, design, config) for s in config["scenarios"] for r in range(config["reps"])}
    judge = judge_mod.make_judge(config, mock=args.mock)
    try:
        n_valid = n_invalid = 0
        for i, cell in enumerate(cells, 1):
            results = run_cell(cell, instances, config, judge, cost, runs_dir, args.mock)
            n_invalid += sum(1 for r in results if r["status"] != "valid")
            n_valid += results[-1]["status"] == "valid"
            if uses_repo(config):
                for r in results:   # per-run input, output and cache tokens, for the cost estimate (also kept in run_tokens.jsonl)
                    print("        %s: %s" % (r["run_id"], token_line(r)), flush=True)
                    with open(out_root / "run_tokens.jsonl", "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"run_id": r["run_id"], "builder": r["builder"], "condition": r["condition"],
                                             "status": r["status"], "usage_total": r.get("usage_total"),
                                             "turns": r.get("turns"), "eval_calls_used": r.get("eval_calls_used"),
                                             "cost_usd": r.get("cost_usd")}) + "\n")
            print("[%2d/%d] %s rep%d %s %s%s -> %s  (spent $%.2f)" % (
                i, len(cells), cell[0], cell[1], cell[2], cell[3], " " + cell[4] if len(cell) > 4 else "",
                "valid" if results[-1]["status"] == "valid" else "INVALID (%s)" % results[-1]["invalid_reason"], cost.spent), flush=True)
    except bl.CapExceeded as exc:
        print("ABORTED: spending cap: %s" % exc)
        return 3
    except bl.ProviderAccountError as exc:
        print("ABORTED: provider account problem (fix it, then rerun; finished runs are kept): %s" % exc)
        return 4
    print("done: %d valid, %d invalid attempts logged, total spent $%.2f" % (n_valid, n_invalid, cost.spent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
