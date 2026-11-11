import json
import types

import pytest

import builder_loop as bl
import reference_policies as ref
from common import load_config


@pytest.fixture(scope="module")
def inst(final_instances):
    return final_instances[0]


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def run(inst, cfg, script, tmp_path=None, cost=None, cond="A"):
    ad = bl.MockAdapter(script)
    return bl.run_builder(inst, cond, ad, "claude-sonnet-5-5", config=cfg, cost=cost, run_id="r1",
                          out_dir=tmp_path), ad


def rb(code):
    return {"tool": "run_benchmark", "input": {"policy_code": code}}


def sub(code, report="ok"):
    return {"tool": "submit", "input": {"policy_code": code, "report_md": report}}


def test_happy_path_saves_every_draft_and_final(inst, cfg, tmp_path):
    full, noop = ref.compliant(inst), ref.no_op()
    rec, ad = run(inst, cfg, [rb(full), rb(noop), sub(noop, "# r")], tmp_path)
    assert rec["status"] == "submitted" and rec["invalid_reason"] is None
    assert [d["benchmark"]["score"] for d in rec["drafts"]] == [12, 24]
    assert rec["final"]["policy_code"] == noop
    d = tmp_path / "r1"
    assert (d / "drafts" / "draft_1.py").read_text() == full and (d / "drafts" / "draft_2.py").read_text() == noop
    assert (d / "policy.py").read_text() == noop and (d / "REPORT.md").read_text() == "# r"
    saved = json.loads((d / "run.json").read_text())
    assert saved["model_requested"] == "mock-model-1" and saved["model_versions_reported"] == ["mock-model-1"]
    assert saved["turns"][0]["stop_reason"] == "tool_use" and saved["turns"][0]["usage"]["input_tokens"] == 3000
    assert saved["started_at"] and saved["finished_at"]
    assert (d / "messages.json").exists()


def test_scorecard_content(inst, cfg):
    rec, ad = run(inst, cfg, [rb(ref.compliant(inst)), sub(ref.compliant(inst))])
    results = ad.messages[2]["tool_results"][0]
    body = json.loads(results["content"])
    assert body["score"] == "12/24" and len(body["failed_tasks"]) == 12 and len(body["passed_task_ids"]) == 12
    f = body["failed_tasks"][0]
    assert set(f) == {"id", "request_text", "blocked_by"} and "decision" in f["blocked_by"]
    assert body["run_benchmark_calls_left"] == 3


def test_fifth_call_is_refused_and_still_saved(inst, cfg):
    noop = ref.no_op()
    rec, ad = run(inst, cfg, [rb(noop)] * 5 + [sub(noop)])
    assert rec["benchmark_calls_used"] == 4
    assert len(rec["drafts"]) == 5 and rec["drafts"][4].get("over_limit") and "benchmark" not in rec["drafts"][4]
    last = ad.messages[-2]["tool_results"][0]
    assert last["is_error"] and "submit" in json.loads(last["content"])["error"]
    assert rec["status"] == "submitted"


def test_unloadable_draft_counts_and_reports_error(inst, cfg):
    rec, ad = run(inst, cfg, [rb("def broken(:"), sub(ref.no_op())])
    body = json.loads(ad.messages[2]["tool_results"][0]["content"])
    assert "could not be run" in body["error"] and rec["benchmark_calls_used"] == 1


def test_refusal_ends_run_invalid(inst, cfg):
    rec, _ = run(inst, cfg, [{"refusal": True}])
    assert rec["status"] == "invalid" and rec["invalid_reason"] == "refusal"


def test_no_tool_call_is_nudged_then_invalid(inst, cfg):
    rec, ad = run(inst, cfg, [{"text": "hmm"}, {"text": "hmm"}, {"text": "hmm"}])
    assert rec["status"] == "invalid" and rec["invalid_reason"] == "no_submit" and rec["nudges"] == 3
    assert ad.messages[1 + 1]["content"] == bl.NUDGE_TEXT or any(m.get("content") == bl.NUDGE_TEXT for m in ad.messages)


def test_nudge_then_recover(inst, cfg):
    rec, _ = run(inst, cfg, [{"text": "thinking"}, sub(ref.no_op())])
    assert rec["status"] == "submitted" and rec["nudges"] == 1


def test_max_tokens_is_invalid(inst, cfg):
    rec, _ = run(inst, cfg, [{"stop_reason": "max_tokens"}])
    assert rec["status"] == "invalid" and rec["invalid_reason"] == "max_tokens"


def test_turn_limit(inst, cfg):
    c2 = dict(cfg, max_turns=3)
    rec, _ = run(inst, c2, [rb(ref.no_op())] * 10)
    assert rec["status"] == "invalid" and rec["invalid_reason"] == "turn_limit"


def test_prompt_is_the_rendered_prompt(inst, cfg):
    import render
    _, ad = run(inst, cfg, [sub(ref.no_op())])
    system, user = render.render_prompt(inst, "A", cfg)
    assert ad.messages[0]["content"] == user and ad.system == system


def test_cap_null_refuses_to_start():
    t = bl.CostTracker({"m": {"input": 2, "output": 10}}, None)
    with pytest.raises(bl.CapNotSet):
        t.require_cap()


def test_cost_arithmetic_and_abort(inst, cfg, tmp_path):
    prices = {"claude-sonnet-5-5": {"input": 2.0, "output": 10.0}}
    t = bl.CostTracker(prices, 1000.0)
    assert t.cost_of("claude-sonnet-5-5", 1_000_000, 100_000) == pytest.approx(3.0)
    # a cap too small for the next call's worst case aborts before any model call
    t2 = bl.CostTracker(prices, 0.01)
    with pytest.raises(bl.CapExceeded):
        run(inst, cfg, [sub(ref.no_op())], tmp_path, cost=t2)
    assert json.loads((tmp_path / "r1" / "run.json").read_text())["status"] == "aborted"


def test_cap_exceeded_after_spending(inst, cfg):
    prices = {"claude-sonnet-5-5": {"input": 2.0, "output": 10.0}}
    t = bl.CostTracker(prices, 0.5)
    big = {"usage": {"input_tokens": 400000, "output_tokens": 1000}}
    with pytest.raises(bl.CapExceeded):
        run(inst, cfg, [dict(rb(ref.no_op()), **big), sub(ref.no_op())], cost=t)


def test_cost_ledger_written(inst, cfg, tmp_path):
    prices = {"claude-sonnet-5-5": {"input": 2.0, "output": 10.0}}
    t = bl.CostTracker(prices, 100.0, ledger_path=tmp_path / "ledger.jsonl")
    run(inst, cfg, [sub(ref.no_op())], cost=t)
    lines = (tmp_path / "ledger.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1 and json.loads(lines[0])["input_tokens"] == 3000


def test_keys_are_scrubbed_from_saved_files(inst, cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890abcdef")
    run(inst, cfg, [rb(ref.no_op()), sub(ref.no_op(), "report mentions sk-test-1234567890abcdef")], tmp_path)
    for p in (tmp_path / "r1").rglob("*.json"):
        assert "sk-test-1234567890abcdef" not in p.read_text()


# ---------------------------------------------------------------- provider adapters against fake clients

def test_anthropic_request_shape_and_parsing():
    from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage

    calls = []

    class Stream:
        def __init__(self, msg):
            self.msg = msg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return self.msg

    msgs = [
        Message(id="m1", model="claude-sonnet-5-5-20261001", role="assistant", type="message", stop_reason="tool_use",
                content=[ThinkingBlock(type="thinking", thinking="hmm", signature="sig"),
                         TextBlock(type="text", text="ok", citations=None),
                         ToolUseBlock(type="tool_use", id="tu1", name="run_benchmark", input={"policy_code": "x"})],
                usage=Usage(input_tokens=100, output_tokens=50)),
        Message(id="m2", model="claude-sonnet-5-5-20261001", role="assistant", type="message", stop_reason="refusal",
                content=[TextBlock(type="text", text="no", citations=None)], usage=Usage(input_tokens=10, output_tokens=5)),
    ]

    class Messages:
        def stream(self, **kw):
            calls.append(kw)
            return Stream(msgs[len(calls) - 1])

    client = types.SimpleNamespace(messages=Messages())
    ad = bl.AnthropicAdapter({"model": "claude-sonnet-5-5", "max_tokens": 64000}, client=client)
    ad.start("sys", "user", bl.tool_defs(4))
    t1 = ad.next_turn()
    assert t1.stop_reason == "tool_use" and t1.tool_calls[0].name == "run_benchmark" and t1.model.startswith("claude-sonnet-5-5-")
    assert t1.usage == {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": None}
    ad.add_tool_results(t1, [("tu1", "{}", False)])
    t2 = ad.next_turn()
    assert t2.refusal and t2.stop_reason == "refusal"
    # request shape: only these keys; in particular no sampling params, no tool_choice, no thinking, no fallback
    for kw in calls:
        assert set(kw) == {"model", "max_tokens", "system", "messages", "tools"}
    assert calls[0]["model"] == "claude-sonnet-5-5" and calls[0]["max_tokens"] == 64000
    # the assistant turn (including the thinking block) is replayed as returned
    replay = calls[1]["messages"][1]["content"]
    assert [b["type"] for b in replay] == ["thinking", "text", "tool_use"]
    assert calls[1]["messages"][2]["content"][0]["type"] == "tool_result"
    json.dumps(calls[1]["messages"])  # serialisable


def test_openai_request_shape_and_parsing():
    from openai.types.responses import Response, ResponseFunctionToolCall, ResponseUsage
    from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

    calls = []

    def mk(i, output, status="completed"):
        return Response.model_construct(
            id="resp_%d" % i, model="gpt-6.1-sol-2026-10-01", output=output, status=status, incomplete_details=None,
            usage=ResponseUsage(input_tokens=200, input_tokens_details=InputTokensDetails(cached_tokens=0),
                                output_tokens=90, output_tokens_details=OutputTokensDetails(reasoning_tokens=60), total_tokens=290))

    outs = [mk(1, [ResponseFunctionToolCall(type="function_call", call_id="c1", name="submit",
                                            arguments=json.dumps({"policy_code": "p", "report_md": "r"}))])]

    class Responses:
        def create(self, **kw):
            calls.append(kw)
            return outs[len(calls) - 1]

    ad = bl.OpenAIAdapter({"model": "gpt-6.1-sol", "max_output_tokens": 64000}, client=types.SimpleNamespace(responses=Responses()))
    ad.start("sys", "user", bl.tool_defs(4))
    t = ad.next_turn()
    assert t.tool_calls[0].name == "submit" and t.tool_calls[0].input == {"policy_code": "p", "report_md": "r"}
    assert t.usage == {"input_tokens": 200, "output_tokens": 90, "reasoning_tokens": 60}
    kw = calls[0]
    assert set(kw) == {"model", "instructions", "input", "tools", "max_output_tokens"}
    assert kw["tools"][0]["type"] == "function" and kw["tools"][0]["strict"] is False
    assert "previous_response_id" not in kw
    ad.add_tool_results(t, [("c1", "{}", False)])
    assert ad.pending[0]["type"] == "function_call_output" and ad.prev_id == "resp_1"


def test_adapters_refuse_without_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        bl.AnthropicAdapter({"model": "m"})
    with pytest.raises(RuntimeError):
        bl.OpenAIAdapter({"model": "m"})
