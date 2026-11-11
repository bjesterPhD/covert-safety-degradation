import json

import generate
from common import PAIRS, instance_seed, sha256_hex


def test_instance_seed_formula():
    assert instance_seed("S1", 0) == sha256_hex("S1", 0, "quickcheck-v1")
    assert instance_seed("S1", 0) != instance_seed("S1", 1)
    assert instance_seed("S1", 0) != instance_seed("S2", 0)


def test_generation_is_deterministic(design):
    a = generate.generate_instance("S1", 2, design)
    b = generate.generate_instance("S1", 2, generate.build_design())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_ten_final_instances(final_instances):
    assert len(final_instances) == 10
    assert len({i["instance_id"] for i in final_instances}) == 10


def test_each_pair_splits_exactly_five_five(final_instances):
    for pair in PAIRS:
        a = sum(1 for i in final_instances if i["roles"][pair] == "a")
        assert a == 5, "%s: %d a-costly" % (pair, a)


def test_roles_balanced_within_a_pair_per_scenario_to_three_two(final_instances):
    for pair in PAIRS:
        for scen in ("S1", "S2"):
            a = sum(1 for i in final_instances if i["scenario_id"] == scen and i["roles"][pair] == "a")
            assert a in (2, 3)


def test_each_instance_has_two_pairs_in_each_orientation_not_forced(final_instances):
    # the design must not make roles identical across pairs within an instance
    patterns = {tuple(i["roles"][p] for p in PAIRS) for i in final_instances}
    assert len(patterns) >= 5


def test_top_half_balance(final_instances):
    top_mech = {m: 0 for m in PAIRS}
    top_role = {"costly": 0, "free": 0}
    cell = {}
    for inst in final_instances:
        by_id = {c["id"]: c for c in inst["constraints"]}
        for pos, cid in enumerate(inst["display_order"], 1):
            if pos <= 4:
                c = by_id[cid]
                top_mech[c["mechanism"]] += 1
                top_role[c["role"]] += 1
                cell[(c["mechanism"], c["role"])] = cell.get((c["mechanism"], c["role"]), 0) + 1
    assert set(top_mech.values()) == {10}
    assert top_role == {"costly": 20, "free": 20}
    assert set(cell.values()) == {5} and len(cell) == 8


def test_display_order_is_a_permutation_of_the_eight(final_instances):
    for inst in final_instances:
        assert sorted(inst["display_order"]) == sorted(c["id"] for c in inst["constraints"])


def test_display_orders_differ_between_instances(final_instances):
    assert len({tuple(i["display_order"]) for i in final_instances}) >= 8


def test_categories_distinct_and_rate_floor(all_instances):
    for inst in all_instances:
        v = inst["values"]
        assert v["cat_1"] != v["cat_2"]
        assert v["K_rate_minus_1"] == max(2, v["K_rate"] - 1) >= 2
        cons = {c["id"]: c for c in inst["constraints"]}
        assert cons["rate.a"]["K"] == v["K_rate"] and cons["rate.b"]["K"] == v["K_rate_minus_1"]
        assert cons["refusal.a"]["category"] == v["cat_1"] and cons["refusal.b"]["category"] == v["cat_2"]


def test_exactly_four_costly_and_four_free(all_instances):
    for inst in all_instances:
        assert sorted(c["role"] for c in inst["constraints"]) == ["costly"] * 4 + ["free"] * 4
        for pair in PAIRS:
            roles = {c["side"]: c["role"] for c in inst["constraints"] if c["pair"] == pair}
            assert set(roles.values()) == {"costly", "free"}


def test_paired_instances_same_for_both_builders():
    # instance generation takes no builder or condition input
    a = generate.generate_instance("S2", 3, generate.build_design())
    assert "builder" not in a and "condition" not in a


def test_s1_example_slot_names_present(final_instances):
    s1 = [i for i in final_instances if i["scenario_id"] == "S1"][0]
    for k in ("repo", "project_dir", "docs_dir", "shared_dir", "K_rate", "K_rate_minus_1", "cat_1", "cat_2"):
        assert k in s1["values"]


def test_balance_report_runs(final_instances):
    text = generate.design_balance_report(final_instances)
    assert "5 / 5" in text and "by mechanism" in text
