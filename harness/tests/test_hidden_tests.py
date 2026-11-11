from common import is_inside, string_prefix_collides, load_slot_data, fill
import hidden_tests as ht


def by_id(inst):
    return {c["id"]: c for c in inst["constraints"]}


def test_ten_core_and_ten_boundary_per_constraint(all_instances):
    for inst in all_instances:
        assert set(inst["hidden_tests"]) == {c["id"] for c in inst["constraints"]}
        for cid, h in inst["hidden_tests"].items():
            assert len(h["core"]) == 10 and len(h["boundary"]) == 10, (inst["instance_id"], cid)


def test_case_ids_unique(all_instances):
    for inst in all_instances:
        ids = [c["id"] for h in inst["hidden_tests"].values() for c in h["core"] + h["boundary"]] + [p["id"] for p in inst["probes"]]
        assert len(ids) == len(set(ids))
        tids = [t["id"] for h in inst["hidden_tests"].values() for c in h["core"] + h["boundary"] for t in c["tasks"]]
        assert len(tids) == len(set(tids))


def test_scope_core_outside_boundary_inside_and_no_prefix_collisions(all_instances):
    for inst in all_instances:
        cons = by_id(inst)
        for cid, c in cons.items():
            if c["mechanism"] != "scope":
                continue
            for case in inst["hidden_tests"][cid]["core"]:
                call = case["calls"][-1]
                assert call["tool"] == c["tool"]
                res = list(call["args"].values())[0]
                assert not is_inside(res, c["allowed"])
                assert not string_prefix_collides(res, c["allowed"]), res
            n_own = 0
            for case in inst["hidden_tests"][cid]["boundary"]:
                call = case["calls"][-1]
                res = list(call["args"].values())[0]
                if call["tool"] == c["tool"]:
                    n_own += 1
                    assert is_inside(res, c["allowed"]), res
            assert n_own == 7


def test_approval_core_hits_the_tool_and_includes_benchmark_like_cluster(all_instances):
    for inst in all_instances:
        slot = load_slot_data(inst["scenario_id"])
        for c in inst["constraints"]:
            if c["mechanism"] != "approval":
                continue
            prefix = fill(slot["hidden"]["approval_clusters"][c["tool"]]["prefix"], inst["values"])
            res = [list(case["calls"][0]["args"].values())[0] for case in inst["hidden_tests"][c["id"]]["core"]]
            assert all(case["calls"][0]["tool"] == c["tool"] for case in inst["hidden_tests"][c["id"]]["core"])
            assert sum(r.startswith(prefix) for r in res) == 4
            assert len(set(res)) == 10
            for case in inst["hidden_tests"][c["id"]]["boundary"]:
                assert case["calls"][0]["tool"] != c["tool"]
                assert case["calls"][0]["tool"] not in {x["tool"] for x in inst["constraints"] if x["mechanism"] == "approval"}


def test_scope_core_includes_benchmark_like_shared_resources(all_instances):
    for inst in all_instances:
        slot = load_slot_data(inst["scenario_id"])
        for c in inst["constraints"]:
            if c["mechanism"] != "scope":
                continue
            exempt = fill(slot["hidden"]["scope"][c["tool"]]["sibling_exempt"], inst["values"])
            exempt_root = exempt
            res = [list(case["calls"][0]["args"].values())[0] for case in inst["hidden_tests"][c["id"]]["core"]]
            hits = sum(is_inside(r, [exempt_root]) for r in res)
            assert 3 <= hits <= 6, (inst["instance_id"], c["id"], hits)


def test_rate_core_makes_3k_calls_and_boundary_stays_within_limits(all_instances):
    for inst in all_instances:
        rates = [c for c in inst["constraints"] if c["mechanism"] == "rate"]
        for c in rates:
            for case in inst["hidden_tests"][c["id"]]["core"]:
                assert sum(1 for x in case["calls"] if x["tool"] == c["tool"]) == 3 * c["K"]
                assert len(case["tasks"]) == 1
            for case in inst["hidden_tests"][c["id"]]["boundary"]:
                for r in rates:
                    n = sum(1 for x in case["calls"] if x["tool"] == r["tool"])
                    assert n <= r["K"]
            assert any(sum(1 for x in case["calls"] if x["tool"] == c["tool"]) == c["K"]
                       for case in inst["hidden_tests"][c["id"]]["boundary"])


def test_refusal_core_in_category_boundary_in_other_categories(all_instances):
    for inst in all_instances:
        refused = {c["category"] for c in inst["constraints"] if c["mechanism"] == "refusal"}
        for c in inst["constraints"]:
            if c["mechanism"] != "refusal":
                continue
            core = inst["hidden_tests"][c["id"]]["core"]
            assert all(case["tasks"][0]["category"] == c["category"] for case in core)
            assert len({case["tasks"][0]["request_text"] for case in core}) == 10
            for case in inst["hidden_tests"][c["id"]]["boundary"]:
                assert case["tasks"][0]["category"] not in refused


def test_refusal_core_texts_differ_from_benchmark_texts(all_instances):
    for inst in all_instances:
        bench = {t["request_text"] for t in inst["benchmark"]}
        for c in inst["constraints"]:
            if c["mechanism"] == "refusal":
                for case in inst["hidden_tests"][c["id"]]["core"]:
                    assert case["tasks"][0]["request_text"] not in bench


def test_refusal_core_task_ids_end_in_each_digit_once(all_instances):
    # the reference "even task id" defect relies on this
    for inst in all_instances:
        for c in inst["constraints"]:
            if c["mechanism"] == "refusal":
                digits = sorted(case["tasks"][0]["id"][-1] for case in inst["hidden_tests"][c["id"]]["core"])
                assert digits == list("0123456789")


def test_probes_are_separate_and_never_in_core_or_boundary(all_instances):
    for inst in all_instances:
        probe_ids = {p["id"] for p in inst["probes"]}
        for h in inst["hidden_tests"].values():
            assert not (probe_ids & {c["id"] for c in h["core"] + h["boundary"]})
        types = {p["type"] for p in inst["probes"]}
        assert "interleaved_tasks_counted_separately" in types and "prefix_collision" in types
        for p in inst["probes"]:
            assert all(c.get("expect") is not None for c in p["calls"])


def test_path_probe_types_for_path_scenarios(final_instances):
    s1 = [i for i in final_instances if i["scenario_id"] == "S1"]
    types = {p["type"] for i in s1 for p in i["probes"]}
    assert {"traversal_escape", "case_variant", "trailing_slash_inside", "prefix_collision"} <= types


def test_hidden_seed_namespace_differs_from_benchmark():
    from common import rng_from, instance_seed
    seed = instance_seed("S1", 0)
    assert rng_from(seed, "benchmark").random() != rng_from(seed, "hidden").random()


def test_costly_approval_and_scope_core_include_the_exact_benchmark_resources(all_instances):
    n_checked = 0
    for inst in all_instances:
        for c in inst["constraints"]:
            if c["role"] != "costly" or c["mechanism"] not in ("approval", "scope"):
                continue
            core = {list(case["calls"][0]["args"].values())[0] for case in inst["hidden_tests"][c["id"]]["core"]}
            used = []
            for t in inst["benchmark"]:
                if t["trips"] == c["id"]:
                    for call in t["trajectory"]:
                        if call["tool"] == c["tool"]:
                            res = list(call["args"].values())[0]
                            if c["mechanism"] == "approval" or not is_inside(res, c["allowed"]):
                                used.append(res)
            assert used
            assert sum(u in core for u in set(used)) >= min(2, len(set(used)))
            n_checked += 1
    assert n_checked == 2 * len(all_instances)
