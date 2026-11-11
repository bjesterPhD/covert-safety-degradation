"""OpenRouterAdapter against canned responses built as real openai SDK objects (no network)."""
import json

import pytest
from openai.types.chat import ChatCompletion

import builder_loop as bl

CFG = {"model": "openai/gpt-6.1-sol", "upstream_providers": ["openai"], "max_output_tokens": 64000}
TOOLS = [{"name": "submit", "description": "submit", "parameters": {"type": "object", "properties": {}}}]


def completion(provider="OpenAI", model="openai/gpt-6.1-sol", tool=True):
    msg = {"role": "assistant", "content": None if tool else "done",
           "reasoning_details": [{"type": "reasoning.encrypted", "data": "x"}]}
    if tool:
        msg["tool_calls"] = [{"id": "call_1", "type": "function",
                              "function": {"name": "submit", "arguments": json.dumps({"policy_code": "p", "report_md": "r"})}}]
    return ChatCompletion.model_validate({
        "id": "gen-1", "object": "chat.completion", "created": 0, "model": model, "provider": provider,
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool else "stop", "message": msg}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cost": 0.004,
                  "completion_tokens_details": {"reasoning_tokens": 150}}})


class FakeClient(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_routing_is_pinned_and_no_sampling_params_are_sent():
    client = FakeClient([completion()])
    a = bl.OpenRouterAdapter(CFG, client=client)
    a.start("sys", "user", TOOLS)
    turn = a.next_turn()
    sent = client.calls[0]
    assert sent["extra_body"]["provider"] == {"order": ["openai"], "only": ["openai"], "allow_fallbacks": False}
    for k in ("temperature", "top_p", "seed", "tool_choice"):
        assert k not in sent
    assert turn.stop_reason == "tool_use" and turn.tool_calls[0].name == "submit"
    assert turn.usage == {"input_tokens": 1000, "output_tokens": 200, "reasoning_tokens": 150}
    assert turn.raw["upstream_provider"] == "openai" and turn.raw["openrouter_cost_usd"] == 0.004


def test_reasoning_details_and_tool_results_go_back_into_the_conversation():
    client = FakeClient([completion(), completion(tool=False)])
    a = bl.OpenRouterAdapter(CFG, client=client)
    a.start("sys", "user", TOOLS)
    t = a.next_turn()
    a.add_tool_results(t, [("call_1", "{\"ok\": true}", False)])
    a.next_turn()
    msgs = client.calls[1]["messages"]
    assert msgs[2]["role"] == "assistant" and msgs[2]["reasoning_details"]
    assert msgs[3] == {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\": true}"}


@pytest.mark.parametrize("provider,model", [("Azure", "openai/gpt-6.1-sol"), ("OpenAI", "openai/gpt-6.1-terra")])
def test_a_different_upstream_or_model_is_refused(provider, model):
    a = bl.OpenRouterAdapter(CFG, client=FakeClient([completion(provider=provider, model=model)]))
    a.start("sys", "user", TOOLS)
    with pytest.raises(bl.UpstreamMismatch):
        a.next_turn()


def test_openrouter_credit_errors_abort_the_batch():
    assert bl.is_account_error(Exception("Error code: 402 - {'error': {'message': 'Insufficient credits'}}"))
