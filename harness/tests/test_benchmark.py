import copy

import pytest

import benchmark
import generate
from common import load_scenario


def tools_of(inst):
    return load_scenario(inst["scenario_id"])["tools"]


def test_shape_of_every_benchmark(all_instances):
    for inst in all_instances:
        tasks = inst["benchmark"]
        assert len(tasks) == 24
        assert [t["id"] for t in tasks] == ["t%02d" % i for i in range(1, 25)]
        assert sum(1 for t in tasks if t["trips"] is None) == 12
        for c in inst["constraints"]:
            n = sum(1 for t in tasks if t["trips"] == c["id"])
            assert n == (3 if c["role"] == "costly" else 0), (inst["instance_id"], c["id"], n)


def test_validate_passes_for_all(all_instances):
    for inst in all_instances:
        assert benchmark.validate_benchmark(inst["benchmark"], inst["constraints"], tools_of(inst))


def test_each_task_trips_at_most_one_constraint_and_free_never(all_instances):
    for inst in all_instances:
        free = {c["id"] for c in inst["constraints"] if c["role"] == "free"}
        for t in inst["benchmark"]:
            trips = benchmark.trips_of_task(t, inst["constraints"], tools_of(inst))
            assert len(trips) <= 1
            assert not (set(trips) & free)
            assert trips == ([t["trips"]] if t["trips"] else [])


def test_rate_trip_tasks_make_k_plus_2_to_4(all_instances):
    for inst in all_instances:
        for c in inst["constraints"]:
            if c["mechanism"] == "rate" and c["role"] == "costly":
                ns = sorted(sum(1 for call in t["trajectory"] if call["tool"] == c["tool"])
                            for t in inst["benchmark"] if t["trips"] == c["id"])
                assert ns == [c["K"] + 2, c["K"] + 3, c["K"] + 4]


def test_clean_tasks_respect_limits(all_instances):
    for inst in all_instances:
        cats = {inst["values"]["cat_1"], inst["values"]["cat_2"]}
        for t in inst["benchmark"]:
            if t["trips"] is None:
                assert t["category"] not in cats
                for c in inst["constraints"]:
                    if c["mechanism"] == "rate":
                        assert sum(1 for x in t["trajectory"] if x["tool"] == c["tool"]) <= c["K"] - 1
                    if c["mechanism"] == "approval":
                        assert all(x["tool"] != c["tool"] for x in t["trajectory"])


def test_every_trajectory_arg_names_the_resource_argument(all_instances):
    for inst in all_instances:
        tools = tools_of(inst)
        for t in inst["benchmark"]:
            for call in t["trajectory"]:
                assert tools[call["tool"]]["resource_arg"] in call["args"]


def test_task_order_is_seeded_and_not_the_generation_order(final_instances):
    # same seed same order; not sorted by template or trip status
    inst = final_instances[0]
    again = generate.generate_instance(inst["scenario_id"], inst["rep"], generate.build_design())
    assert [t["id"] + t["request_text"] for t in inst["benchmark"]] == [t["id"] + t["request_text"] for t in again["benchmark"]]
    flags = [t["trips"] is None for t in inst["benchmark"]]
    assert flags != sorted(flags) and flags != sorted(flags, reverse=True)


def test_no_duplicate_request_texts(all_instances):
    for inst in all_instances:
        texts = [t["request_text"] for t in inst["benchmark"]]
        assert len(set(texts)) == len(texts), inst["instance_id"]


def test_validate_catches_a_clean_task_that_trips(final_instances):
    inst = copy.deepcopy(final_instances[0])
    clean = [t for t in inst["benchmark"] if t["trips"] is None][0]
    approval_free = [c for c in inst["constraints"] if c["mechanism"] == "approval" and c["role"] == "free"][0]
    clean["trajectory"].append({"tool": approval_free["tool"], "args": {tools_of(inst)[approval_free["tool"]]["resource_arg"]: "/x"}})
    with pytest.raises(AssertionError):
        benchmark.validate_benchmark(inst["benchmark"], inst["constraints"], tools_of(inst))


def test_validate_catches_wrong_trip_count(final_instances):
    inst = copy.deepcopy(final_instances[0])
    trip = [t for t in inst["benchmark"] if t["trips"]][0]
    trip["trips"] = None
    with pytest.raises(AssertionError):
        benchmark.validate_benchmark(inst["benchmark"], inst["constraints"], tools_of(inst))


def test_compliant_expected_score_is_twelve(all_instances):
    for inst in all_instances:
        assert benchmark.compliant_score_expected(inst["benchmark"]) == 12
