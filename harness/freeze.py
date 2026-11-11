"""Freeze: record the date and a SHA-256 for every file that defines the protocol, and
check later that none of them has changed.

    python3 harness/freeze.py --config openscope --write     # open-scope version: ANALYSIS_PLAN_OPENSCOPE.md and FREEZE_OPENSCOPE.md
    python3 harness/freeze.py --config openscope --verify
    python3 harness/freeze.py --config evalshaped --write     # eval-shaped version: ANALYSIS_PLAN_EVALSHAPED.md and FREEZE_EVALSHAPED.md
    python3 harness/freeze.py --config evalshaped --verify

Each protocol has its own freeze record, plan file and tag, chosen from the config's `protocol`. They are
independent: verifying one never looks at the other's files.

run_batch.py calls verify() before any real run into the final table and refuses to start on a mismatch. After a
freeze, changing any frozen file means a new freeze, and any final runs made under the old one are rerun.
"""
import argparse
import datetime
import hashlib
import sys

from common import ROOT, load_config, add_config_arg, select_config, arms_of, is_openscope, is_evalshaped, config_flag

# ---- open-scope version ----------------------------------------------------------------------------------------
OPENSCOPE_GLOBS = ["DESIGN_OPENSCOPE.md", "ANALYSIS_PLAN_OPENSCOPE.md", "config/*", "scenarios/*.yaml",
                   "harness/*.py", "harness/judge_sanity_openscope/*", "harness/tests/*.py",
                   "results/openscope/development/rendered_prompts/*.md"]

OPENSCOPE_TAG = "freeze-openscope-v1"

OPENSCOPE_POINTER = ("**Pointer:** DESIGN_OPENSCOPE.md sections 1, 2 and 6 to 11 (why, what changes, builders and size, cost, reuse, threats, "
                     "open questions, the build notes) are part of the frozen design but are not repeated here.")

# ---- eval-shaped version ---------------------------------------------------------------------------------------
EVALSHAPED_GLOBS = ["DESIGN_OPENSCOPE.md", "DESIGN_EVALSHAPED.md", "ANALYSIS_PLAN_EVALSHAPED.md", "config/*", "scenarios/*.yaml",
                    "harness/*.py", "harness/judge_sanity_openscope/*", "harness/judge_sanity_evalshaped/*",
                    "harness/tests/*.py", "results/evalshaped/development/rendered_prompts/*.md"]

EVALSHAPED_TAG = "freeze-evalshaped-v1"

EVALSHAPED_POINTER = ("**Pointer:** DESIGN_EVALSHAPED.md sections 1 and 6 to 10 (why, builders and size, reuse, threats, what is not in this study, "
                      "the build notes) are part of the frozen design but are not repeated here.")


def _openscope_plan(root):
    lines = (root / "DESIGN_OPENSCOPE.md").read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("## 3. "))
    end = next(i for i, l in enumerate(lines) if l.startswith("## 6. "))
    body = "\n".join(lines[start:end]).rstrip() + "\n"
    return ("# Analysis plan for the open-scope version (frozen)\n\nCopied verbatim from DESIGN_OPENSCOPE.md sections 3 to 5 "
            "(the latent properties and coverage rules, the eval arithmetic and the incentive gate, the measures, the validity gates "
            "and the reading rule) at freeze time. If the two ever differ, this file is the pre-registered plan.\n\n" +
            OPENSCOPE_POINTER + "\n\n" + body)


def _evalshaped_plan(root):
    lines = (root / "DESIGN_EVALSHAPED.md").read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("## 2. "))
    end = next(i for i, l in enumerate(lines) if l.startswith("## 6. "))
    body = "\n".join(lines[start:end]).rstrip() + "\n"
    return ("# Analysis plan for the eval-shaped version (frozen)\n\nCopied verbatim from DESIGN_EVALSHAPED.md sections 2 to 5 (what the builder "
            "gets, the visible and held-out probes and the reference scores, the measures and gates, and the reading rule) at freeze time. If the "
            "two ever differ, this file is the pre-registered plan.\n\n" + EVALSHAPED_POINTER + "\n\n" + body)


SPECS = {
    "evalshaped-v1": {"tag": EVALSHAPED_TAG, "freeze_file": "FREEZE_EVALSHAPED.md", "plan_file": "ANALYSIS_PLAN_EVALSHAPED.md",
                      "globs": EVALSHAPED_GLOBS, "plan": _evalshaped_plan},
    "openscope-v1": {"tag": OPENSCOPE_TAG, "freeze_file": "FREEZE_OPENSCOPE.md", "plan_file": "ANALYSIS_PLAN_OPENSCOPE.md",
                     "globs": OPENSCOPE_GLOBS, "plan": _openscope_plan},
}


def spec_for(config=None):
    """The freeze spec of a config's protocol (default: the config currently selected)."""
    config = config or load_config()
    try:
        return SPECS[config["protocol"]]
    except KeyError:
        raise ValueError("no freeze spec for protocol %r" % config.get("protocol"))


def frozen_files(spec=None, root=ROOT):
    spec = spec or SPECS["openscope-v1"]
    out = set()
    for g in spec["globs"]:
        out.update(p for p in root.glob(g) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(out)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_plan(spec=None, root=ROOT):
    spec = spec or SPECS["openscope-v1"]
    (root / spec["plan_file"]).write_text(spec["plan"](root), encoding="utf-8")


def write_freeze(config=None, root=ROOT):
    config = config or load_config()
    spec = spec_for(config)
    freeze_path = root / spec["freeze_file"]
    write_plan(spec, root)
    files = [p for p in frozen_files(spec, root) if p != freeze_path]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = ["| `%s` | `%s` |" % (p.relative_to(root).as_posix(), sha256(p)) for p in files]
    seed_line = ("**Hand-review sampling seed:** `%s` (fraction %.0f%%). **Run-order seed:** `%s`. **Role-design seed:** `%s`."
                 % (config["seeds"]["hand_review"], 100 * config["hand_review_fraction"], config["seeds"]["run_order"],
                    config["seeds"]["role_design"]))
    head = ["# Freeze record", "",
            "**Frozen:** %s. **Tag:** `%s`. **Protocol:** `%s`." % (now, spec["tag"], config["protocol"]), "",
            "Nothing in the final table may be run unless every file below still has this hash "
            "(`python3 harness/freeze.py%s --verify`). Changing any of them needs a new freeze, and final runs made "
            "under the old one are rerun." % config_flag(config), "",
            seed_line, ""]
    if arms_of(config):
        head += ["**Arms:** %s. **Scorecard format:** `%s`. **Instances:** reused from the quick check (seed "
                 "namespace `%s`)." % (", ".join("`%s`" % a for a in arms_of(config)), config.get("scorecard_format"),
                                       config.get("instance_seed_namespace")), ""]
    if is_evalshaped(config):
        head += ["**Prompt style:** five launch tickets, a visible safety suite (`run_safety_eval`, at most %d calls) and the capability suite "
                 "(`run_eval`, at most %d), no guideline text (`%s`, reusing `%s`), launch line A `%s`, launch line C `%s`, commit message (`%s`), "
                 "no reviewer. **Conditions:** %s. **Turn limit:** %d. **Prompt cache:** %s. **Refusal categories:** %s. **Coverage rules:** %s. "
                 "**Gates:** %s. **Ticket-position seed:** `%s`. **Instances:** reused from the quick check (seed namespace `%s`)." % (
                     config["max_safety_eval_calls"], config["max_eval_calls"], config.get("slot_data_file"), config.get("base_slot_data_file"),
                     config["launch_line_A"], config["launch_line_C"], config["report_file"], ", ".join(config["conditions"]), config["max_turns"],
                     config.get("prompt_cache"), config["refusal_categories"], config["coverage"], config["gates"],
                     config["seeds"]["ticket_position"], config.get("instance_seed_namespace")), ""]
    elif is_openscope(config):
        head += ["**Prompt style:** five launch tickets and a virtual repository with no guideline text (`%s`, reusing `%s`), launch target %d of "
                 "%d in condition A, commit message (`%s`), no reviewer. **Scorecard format:** `%s`. **Conditions:** %s. **Turn limit:** %d. "
                 "**Eval runs:** at most %d. **Prompt cache:** %s. **Refusal categories:** %s. **Coverage rules:** %s. **Gates:** %s. "
                 "**Ticket-position seed:** `%s`. **Instances:** reused from the quick check (seed namespace `%s`)." % (
                     config.get("slot_data_file"), config.get("base_slot_data_file"), config["launch_target"], config["n_tasks"],
                     config["report_file"], config.get("scorecard_format"), ", ".join(config["conditions"]), config["max_turns"],
                     config["max_eval_calls"], config.get("prompt_cache"), config["refusal_categories"], config["coverage"],
                     config["gates"], config["seeds"]["ticket_position"], config.get("instance_seed_namespace")), ""]
    head += ["**Builders:** %s. **Judge:** `%s`. **Cap:** $%.2f for the whole study (spent before freeze is in "
             "`results/spend_ledger.jsonl`)." % (", ".join("`%s`" % b["model"] for b in config["builders"].values()),
                                                 config["judge"]["model"], config["cap"]), "",
             "| File | SHA-256 |", "|---|---|"]
    freeze_path.write_text("\n".join(head + rows) + "\n", encoding="utf-8")
    return len(files)


def verify(config_or_spec=None, root=ROOT):
    """Returns a list of problems; empty means every frozen file is unchanged and nothing new was added."""
    if config_or_spec is None:
        spec = SPECS["openscope-v1"]
    elif "protocol" in config_or_spec:
        spec = spec_for(config_or_spec)
    else:
        spec = config_or_spec
    freeze_path = root / spec["freeze_file"]
    if not freeze_path.exists():
        return ["%s does not exist" % spec["freeze_file"]]
    recorded = {}
    for line in freeze_path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("| `") and line.count("`") >= 4:
            parts = line.split("`")
            recorded[parts[1]] = parts[3]
    problems = []
    current = {p.relative_to(root).as_posix(): p for p in frozen_files(spec, root) if p != freeze_path}
    for rel, digest in recorded.items():
        if rel not in current:
            problems.append("missing: %s" % rel)
        elif sha256(current[rel]) != digest:
            problems.append("changed: %s" % rel)
    for rel in current:
        if rel not in recorded:
            problems.append("added after freeze: %s" % rel)
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--verify", action="store_true")
    add_config_arg(ap)
    args = ap.parse_args()
    select_config(args.config)
    config = load_config()
    spec = spec_for(config)
    if args.write:
        n = write_freeze(config)
        print("FREEZE WRITTEN: %d files hashed -> %s, %s" % (n, spec["freeze_file"], spec["plan_file"]))
        return 0
    problems = verify(config)
    if problems:
        print("FREEZE MISMATCH (%d):" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("FREEZE OK: every frozen file matches %s" % spec["freeze_file"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
