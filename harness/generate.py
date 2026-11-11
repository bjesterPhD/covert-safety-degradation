"""Instance generation (DESIGN section 5).

Instance seed = sha256(scenario_id, rep_index, "quickcheck-v1"). It fixes slot values, categories, task
wording and the benchmark. Two things cannot be read off a single instance seed because they have to be
balanced ACROSS the 10 instances: which twin is costly, and the order constraints are listed in. Those come from
a fixed balanced design computed once from a design seed (config: seeds.role_design) and keyed by
(scenario, rep). Instances outside the 10 (the dev scenario S0) take roles and order from their own seed.

Usage:
    python3 harness/generate.py                  # writes instances for S1,S2 x reps 0-4 and prints the balance
    python3 harness/generate.py --scenario S0 --reps 4
"""
import argparse
import itertools
from pathlib import Path

from common import (
    ROOT, PAIRS, SIDES, FINAL_SCENARIOS, PROTOCOL, fill, instance_seed, load_config, load_scenario,
    load_slot_data, rng_from, write_json, read_json, add_config_arg, select_config, table_dir, is_realworld, is_incidental, is_openscope, is_evalshaped,
)
from benchmark import build_benchmark
from hidden_tests import build_hidden_tests

MECH_ORDER = PAIRS  # mechanism index 0..3


# ------------------------------------------------------------------------------ values and constraints

def build_values(scenario, seed_hex):
    """Draw every pool slot. Pools are drawn in file order, but templated entries (e.g. /srv/{repo}) are
    resolved after all plain entries have been drawn."""
    rng = rng_from(seed_hex, "values")
    raw = {}
    for name, pool in scenario["pools"].items():
        if name == "K_rate":
            raw["K_rate"] = int(rng.choice(pool))
        elif name == "categories":
            raw["cat_1"], raw["cat_2"] = rng.sample(list(pool), 2)
        else:
            raw[name] = rng.choice(pool)
    values = {k: v for k, v in raw.items() if not (isinstance(v, str) and "{" in v)}
    pending = {k: v for k, v in raw.items() if k not in values}
    for _ in range(5):
        for k in list(pending):
            try:
                values[k] = fill(pending[k], values)
                del pending[k]
            except KeyError:
                pass
    assert not pending, "unresolved pool entries: %s" % pending
    values["K_rate_minus_1"] = max(2, values["K_rate"] - 1)
    values["categories_pool"] = list(scenario["pools"]["categories"])
    # keep pool order stable in the output
    return {k: values[k] for k in list(raw) + ["K_rate_minus_1", "categories_pool"] if k in values}


def build_constraints(scenario, values, roles, realworld=False, rate_limit=5):
    """Eight constraint dicts. roles[pair] is the side ('a' or 'b') that the benchmark makes costly.

    realworld=True (DESIGN_REALWORLD.md section 2, "Changes to the generated content"): the guideline wording is the
    same for both twins, so both scope twins mean the same region (the b twin's: project + docs, or work + ref
    schemas), and both rate twins carry the strict reference's "handful" (rate_limit, flagged `handful`) instead of
    their own number. Without realworld nothing changes."""
    fmt_values = {k: v for k, v in values.items() if k != "categories_pool"}
    out = []
    for pair in PAIRS:
        for side in SIDES:
            spec = scenario["pairs"][pair][side]
            if realworld and pair == "scope":
                spec = dict(spec, allowed=scenario["pairs"]["scope"]["b"]["allowed"])
            c = {"id": "%s.%s" % (pair, side), "pair": pair, "side": side, "mechanism": spec["mechanism"],
                 "role": "costly" if roles[pair] == side else "free"}
            m = spec["mechanism"]
            if m == "approval":
                c["tool"] = spec["tool"]
            elif m == "scope":
                c["tool"] = spec["tool"]
                c["allowed"] = [fill(a, fmt_values) for a in spec["allowed"]]
            elif m == "rate":
                c["tool"] = spec["tool"]
                if realworld:
                    c["K"] = int(rate_limit)
                    c["handful"] = True
                else:
                    c["K"] = int(fill(str(spec["K"]), fmt_values))
            elif m == "refusal":
                c["category"] = fill(spec["category"], fmt_values)
            else:
                raise ValueError(m)
            out.append(c)
    return out


# ------------------------------------------------------------------------------ balanced design

def _final_instances(reps):
    return [(s, r) for s in FINAL_SCENARIOS for r in range(reps)]


def role_design(design_seed, reps, candidates=400):
    """Exactly balanced costly/free roles per pair over the final instances.

    Each pair is a/b-costly in exactly half of the instances. With an odd rep count, one scenario gets the
    extra a-costly (alternating by pair so the two scenarios stay level). Among `candidates` seeded draws, the one
    with the smallest worst-case correlation between pairs is used.
    """
    keys = _final_instances(reps)
    n = len(keys)
    assert n % 2 == 0
    best, best_score = None, None
    for attempt in range(candidates):
        rng = rng_from(design_seed, "roles", attempt)
        cols = {}
        for p_idx, pair in enumerate(PAIRS):
            heavy = FINAL_SCENARIOS[p_idx % 2]
            col = {}
            for scen in FINAL_SCENARIOS:
                if reps % 2 == 0:
                    n_a = reps // 2
                else:
                    n_a = reps // 2 + (1 if scen == heavy else 0)
                sides = ["a"] * n_a + ["b"] * (reps - n_a)
                rng.shuffle(sides)
                for rep, s in enumerate(sides):
                    col[(scen, rep)] = s
            cols[pair] = col
        worst = 0
        for p, q in itertools.combinations(PAIRS, 2):
            dot = sum((1 if cols[p][k] == "a" else -1) * (1 if cols[q][k] == "a" else -1) for k in keys)
            worst = max(worst, abs(dot))
        score = (worst, attempt)
        if best_score is None or score < best_score:
            best, best_score = cols, score
    return best  # {pair: {(scen,rep): side}}


def _top_matrix(design_seed, n):
    """n rows x 4 mechanisms of 0/1; each row sums to 2, each column to n/2."""
    assert n % 2 == 0
    rng = rng_from(design_seed, "top-matrix")
    subsets = list(itertools.combinations(range(len(MECH_ORDER)), 2))
    for _ in range(200000):
        rows = [rng.choice(subsets) for _ in range(n)]
        col = [0] * len(MECH_ORDER)
        for r in rows:
            for j in r:
                col[j] += 1
        if all(x == n // 2 for x in col):
            return [[1 if j in r else 0 for j in range(len(MECH_ORDER))] for r in rows]
    raise RuntimeError("could not build a balanced top-half matrix")


def order_design(design_seed, reps, roles_cols, candidates=300):
    """Display order per final instance.

    Top half (positions 1-4) holds exactly one twin of each mechanism. For each mechanism, the costly twin is in
    the top half in exactly half of the instances (and the free twin in the other half), and every instance has
    two costly and two free constraints on top. So each mechanism, each role, and each mechanism x role cell
    appears in the top half equally often. Order inside each half is a seeded shuffle, chosen among
    `candidates` draws to equalise mean position across the 8 mechanism x role cells.
    """
    keys = _final_instances(reps)
    matrix = _top_matrix(design_seed, len(keys))
    # which twin is costly in each instance
    best, best_spread = None, None
    for attempt in range(candidates):
        rng = rng_from(design_seed, "order", attempt)
        orders = {}
        sums = {}
        for row, key in zip(matrix, keys):
            top, bottom = [], []
            for j, pair in enumerate(MECH_ORDER):
                costly_side = roles_cols[pair][key]
                free_side = "b" if costly_side == "a" else "a"
                costly_id, free_id = "%s.%s" % (pair, costly_side), "%s.%s" % (pair, free_side)
                if row[j]:
                    top.append(costly_id)
                    bottom.append(free_id)
                else:
                    top.append(free_id)
                    bottom.append(costly_id)
            rng.shuffle(top)
            rng.shuffle(bottom)
            order = top + bottom
            orders[key] = order
            for pos, cid in enumerate(order, 1):
                pair = cid.split(".")[0]
                role = "costly" if roles_cols[pair][key] == cid.split(".")[1] else "free"
                sums.setdefault((pair, role), []).append(pos)
        means = [sum(v) / len(v) for v in sums.values()]
        spread = max(means) - min(means)
        if best_spread is None or (spread, attempt) < best_spread:
            best, best_spread = orders, (spread, attempt)
    return best


def build_design(config=None):
    config = config or load_config()
    # Downstream protocols reuse the quick check's instances byte for byte, so the instance seed namespace must stay put.
    ns = config.get("instance_seed_namespace", PROTOCOL)
    if ns != PROTOCOL:
        raise ValueError("instance_seed_namespace %r differs from %r: instances would no longer be reused" % (ns, PROTOCOL))
    reps = config["reps"]
    design_seed = config["seeds"]["role_design"]
    roles_cols = role_design(design_seed, reps)
    orders = order_design(design_seed, reps, roles_cols)
    return {"roles_cols": roles_cols, "orders": orders, "reps": reps}


def design_balance_report(instances):
    """Plain-text balance report for a list of final instance dicts."""
    lines = []
    n = len(instances)
    lines.append("Balance over %d instances" % n)
    lines.append("")
    lines.append("Costly twin per pair (a-costly / b-costly):")
    for pair in PAIRS:
        a = sum(1 for i in instances if i["roles"][pair] == "a")
        by_scen = {s: sum(1 for i in instances if i["scenario_id"] == s and i["roles"][pair] == "a") for s in FINAL_SCENARIOS}
        lines.append("  %-9s %d / %d   (a-costly by scenario: %s)" % (pair, a, n - a,
                                                                      ", ".join("%s=%d" % kv for kv in by_scen.items())))
    top_mech = {m: 0 for m in PAIRS}
    top_role = {"costly": 0, "free": 0}
    top_cell = {}
    mean_pos = {}
    for inst in instances:
        by_id = {c["id"]: c for c in inst["constraints"]}
        for pos, cid in enumerate(inst["display_order"], 1):
            c = by_id[cid]
            mean_pos.setdefault((c["mechanism"], c["role"]), []).append(pos)
            if pos <= 4:
                top_mech[c["mechanism"]] += 1
                top_role[c["role"]] += 1
                top_cell[(c["mechanism"], c["role"])] = top_cell.get((c["mechanism"], c["role"]), 0) + 1
    lines.append("")
    lines.append("In the top half (positions 1-4), counts over %d instances:" % n)
    lines.append("  by mechanism: " + ", ".join("%s=%d" % kv for kv in top_mech.items()))
    lines.append("  by role:      " + ", ".join("%s=%d" % kv for kv in top_role.items()))
    lines.append("  by mechanism x role: " + ", ".join("%s/%s=%d" % (m, r, top_cell.get((m, r), 0))
                                                        for m in PAIRS for r in ("costly", "free")))
    lines.append("Mean display position (1-8) per mechanism x role:")
    lines.append("  " + ", ".join("%s/%s=%.2f" % (m, r, sum(mean_pos[(m, r)]) / len(mean_pos[(m, r)]))
                                  for m in PAIRS for r in ("costly", "free")))
    return "\n".join(lines)


# ------------------------------------------------------------------------------ instance assembly

def generate_instance(scenario_id, rep, design=None, config=None):
    """Build one instance dict (everything: values, roles, order, benchmark, hidden tests).

    `config` defaults to the config in force (see common.select_config). With the real-world config the surface
    values, roles and display order are exactly the quick check's (same seed, same design), and only the generated
    content named in DESIGN_REALWORLD.md section 2 differs; with any other config the output is as before."""
    config = config if config is not None else load_config()
    if is_evalshaped(config):
        import evalshaped_gen   # imported here: it uses incidental_gen
        return evalshaped_gen.generate_instance(scenario_id, rep, design, config)
    if is_openscope(config):
        import openscope_gen   # imported here: it uses incidental_gen, which uses this module's value and constraint builders
        return openscope_gen.generate_instance(scenario_id, rep, design, config)
    if is_incidental(config):
        import incidental_gen   # imported here: it uses this module's value and constraint builders
        return incidental_gen.generate_instance(scenario_id, rep, design, config)
    rw = is_realworld(config)
    scenario = load_scenario(scenario_id)
    slot_data = load_slot_data(scenario_id)
    seed_hex = instance_seed(scenario_id, rep)
    values = build_values(scenario, seed_hex)

    if design is not None and (scenario_id, rep) in design["orders"]:
        roles = {pair: design["roles_cols"][pair][(scenario_id, rep)] for pair in PAIRS}
        order = list(design["orders"][(scenario_id, rep)])
        design_source = "balanced-design"
    else:
        rng = rng_from(seed_hex, "roles-and-order")
        roles = {pair: rng.choice(SIDES) for pair in PAIRS}
        ids = ["%s.%s" % (p, s) for p in PAIRS for s in SIDES]
        rng.shuffle(ids)
        order = ids
        design_source = "instance-seed"
    constraints = build_constraints(scenario, values, roles, realworld=rw, rate_limit=config.get("strict_rate_limit", 5))
    tasks = build_benchmark(scenario, slot_data, values, constraints, seed_hex, realworld=rw)
    hidden = build_hidden_tests(scenario, slot_data, values, constraints, seed_hex, tasks, realworld=rw)
    tools = [{"name": n, "resource_arg": t["resource_arg"], "description": t["description"]}
             for n, t in scenario["tools"].items()]
    inst = {
        "instance_id": "%s_r%d" % (scenario_id, rep),
        "scenario_id": scenario_id,
        "rep": rep,
        "protocol": PROTOCOL,
        "seed": seed_hex,
        "design_source": design_source,
        "title": scenario["title"],
        "description": " ".join(str(scenario["description"]).split()),
        "tools": tools,
        "values": values,
        "roles": roles,
        "constraints": constraints,
        "display_order": order,
        "benchmark": tasks,
        "hidden_tests": hidden["constraints"],
        "probes": hidden["probes"],
    }
    if rw:
        inst["protocol"] = config["protocol"]
        inst["content_style"] = "realworld"
    return inst


def instances_dir(config=None):
    return table_dir("development", config) / "instances"


def generate_all(out_dir=None, scenarios=None, reps=None, include_s0=True, s0_reps=4):
    config = load_config()
    reps = reps or config["reps"]
    design = build_design(config)
    out_dir = Path(out_dir) if out_dir else instances_dir()
    instances = []
    for scen in (scenarios or FINAL_SCENARIOS):
        for rep in range(reps):
            inst = generate_instance(scen, rep, design, config)
            write_json(out_dir / ("%s.json" % inst["instance_id"]), inst)
            instances.append(inst)
    dev = []
    if include_s0 and not (is_openscope(config) or is_evalshaped(config)):     # the open-scope protocol has no dev-only scenario (smoke runs use S1 rep 0)
        for rep in range(s0_reps):
            inst = generate_instance("S0", rep, None, config)
            write_json(out_dir / ("%s.json" % inst["instance_id"]), inst)
            dev.append(inst)
    return instances, dev


def load_instance(instance_id, out_dir=None):
    return read_json((Path(out_dir) if out_dir else instances_dir()) / ("%s.json" % instance_id))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="output directory (default <results root>/development/instances)")
    ap.add_argument("--no-s0", action="store_true", help="skip the dev-only scenario S0")
    add_config_arg(ap)
    args = ap.parse_args()
    select_config(args.config)
    instances, dev = generate_all(args.out, include_s0=not args.no_s0)
    print("wrote %d final instances and %d dev instances to %s" % (len(instances), len(dev), args.out or instances_dir()))
    print()
    print(design_balance_report(instances))


if __name__ == "__main__":
    main()
