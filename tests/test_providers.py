import json

import pytest

from core.schema import Message, ToolCall, ToolResult, ToolSpec, OpaquePart
from core.providers import ollama, openai_compat, anthropic, gemini
from core.providers.presets import make_provider, PRESETS

TOOL = ToolSpec("run_commands", "run", {"type": "object", "properties": {"commands": {"type": "array"}}})


def _conv():
    return [
        Message.user("open 1abc", context="<state/>"),
        Message("assistant", [ToolCall("c1", "run_commands", {"commands": ["open 1abc"]})]),
        Message.tool_results([ToolResult("c1", "run_commands", '{"ok": true}')]),
        Message.user("now color it red", context="<state2/>"),
    ]


def _fake_stream(monkeypatch, module, lines):
    calls = {}

    def fake(method, url, headers=None, body=None, timeout=0, cancel=None):
        calls["url"] = url
        calls["body"] = body
        calls["headers"] = headers or {}
        return iter(lines)

    monkeypatch.setattr(module, "stream_lines", fake)
    return calls


# ---------------------------------------------------------------- Ollama
def test_ollama_stream_and_messages(monkeypatch):
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "Sure, "}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "run_commands", "arguments": {"commands": ["color #1 red"]}}}]}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 50, "eval_count": 7}),
    ]
    calls = _fake_stream(monkeypatch, ollama, lines)
    p = ollama.OllamaProvider("qwen3:8b")
    deltas = []
    msg, usage = p.stream("SYS", _conv(), [TOOL], on_delta=deltas.append)
    assert msg.text() == "Sure, " and deltas == ["Sure, "]
    assert msg.tool_calls()[0].args == {"commands": ["color #1 red"]}
    assert usage.input_tokens == 50 and usage.output_tokens == 7
    body = calls["body"]
    assert body["model"] == "qwen3:8b" and body["options"]["num_ctx"] >= 16384
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert body["messages"][1]["content"] == "open 1abc"            # old user message: no context
    assert body["messages"][-1]["content"].startswith("<state2/>")  # last user message: with context
    assert body["tools"][0]["function"]["name"] == "run_commands"


# ---------------------------------------------------------------- OpenAI-compatible
def test_openai_stream(monkeypatch):
    def chunk(delta, finish=None, usage=None):
        d = {"choices": [{"delta": delta, "finish_reason": finish}]}
        if usage:
            d["usage"] = usage
        return "data: " + json.dumps(d)
    lines = [
        chunk({"content": "Hel"}), "",
        chunk({"content": "lo"}), "",
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "run_commands", "arguments": "{\"comm"}}]}), "",
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "ands\": [\"view\"]}"}}]}), "",
        chunk({}, "tool_calls", {"prompt_tokens": 12, "completion_tokens": 3}), "",
        "data: [DONE]", "",
    ]
    calls = _fake_stream(monkeypatch, openai_compat, lines)
    p = openai_compat.OpenAICompatProvider("gpt-4.1", api_key="sk-x")
    msg, usage = p.stream("SYS", _conv(), [TOOL])
    assert msg.text() == "Hello"
    tc = msg.tool_calls()[0]
    assert tc.id == "call_1" and tc.args == {"commands": ["view"]}
    assert usage.input_tokens == 12
    body = calls["body"]
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == json.dumps({"commands": ["open 1abc"]})
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}
    assert calls["headers"]["Authorization"] == "Bearer sk-x"


# ---------------------------------------------------------------- Anthropic
def test_anthropic_stream(monkeypatch):
    def ev(t, **kw):
        d = {"type": t}
        d.update(kw)
        return ["event: " + t, "data: " + json.dumps(d), ""]
    lines = []
    lines += ev("message_start", message={"usage": {"input_tokens": 100, "cache_read_input_tokens": 80}})
    lines += ev("content_block_start", index=0, content_block={"type": "thinking", "thinking": ""})
    lines += ev("content_block_delta", index=0, delta={"type": "signature_delta", "signature": "SIG"})
    lines += ev("content_block_stop", index=0)
    lines += ev("content_block_start", index=1, content_block={"type": "text", "text": ""})
    lines += ev("content_block_delta", index=1, delta={"type": "text_delta", "text": "Opening it."})
    lines += ev("content_block_start", index=2, content_block={"type": "tool_use", "id": "toolu_1", "name": "run_commands", "input": {}})
    lines += ev("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": "{\"commands\": [\"op"})
    lines += ev("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": "en 1abc\"]}"})
    lines += ev("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 9})
    lines += ev("message_stop")
    calls = _fake_stream(monkeypatch, anthropic, lines)
    p = anthropic.AnthropicProvider("claude-opus-5", api_key="k")
    msg, usage = p.stream("SYS", _conv(), [TOOL])
    assert msg.text() == "Opening it."
    assert msg.tool_calls()[0].id == "toolu_1" and msg.tool_calls()[0].args == {"commands": ["open 1abc"]}
    assert isinstance(msg.parts[0], OpaquePart) and msg.parts[0].data["signature"] == "SIG"
    assert usage.input_tokens == 100 and usage.cache_read_tokens == 80 and usage.output_tokens == 9
    body = calls["body"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tool_choice"] == {"type": "auto"}
    assert body["tools"][0]["input_schema"] == TOOL.parameters
    assert body["fallbacks"] == "default" and "server-side-fallback" in calls["headers"]["anthropic-beta"]
    # tool results go back as a user message with tool_result blocks
    assert body["messages"][2]["role"] == "user"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert body["messages"][2]["content"][0]["tool_use_id"] == "c1"
    # thinking blocks are echoed back verbatim on the next call
    conv = _conv() + [msg]
    body2 = p._body("SYS", conv, [TOOL])
    assert body2["messages"][-1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "SIG"}


def test_anthropic_haiku_no_adaptive():
    p = anthropic.AnthropicProvider("claude-haiku-4-5", api_key="k")
    assert "thinking" not in p._body("S", [Message.user("hi")], [])


# ---------------------------------------------------------------- Gemini
def test_gemini_stream(monkeypatch):
    lines = [
        "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "Done. "}]}}]}), "",
        "data: " + json.dumps({"candidates": [{"content": {"parts": [{"functionCall": {"name": "get_state", "args": {}}}]},
                                               "finishReason": "STOP"}],
                               "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2}}), "",
    ]
    calls = _fake_stream(monkeypatch, gemini, lines)
    p = gemini.GeminiProvider("gemini-2.5-flash", api_key="g")
    msg, usage = p.stream("SYS", _conv(), [TOOL])
    assert msg.text() == "Done. " and msg.tool_calls()[0].name == "get_state"
    assert usage.input_tokens == 5
    body = calls["body"]
    assert body["system_instruction"]["parts"][0]["text"] == "SYS"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user", "user"]
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "run_commands"
    assert "streamGenerateContent?alt=sse" in calls["url"]
    assert calls["headers"]["x-goog-api-key"] == "g"


def test_presets_and_factory():
    ids = [p["id"] for p in PRESETS]
    assert {"ollama", "gemini", "anthropic", "openai", "openrouter", "groq"} <= set(ids)
    p = make_provider("openai", "x", api_key="k", base_url="https://openrouter.ai/api/v1")
    assert isinstance(p, openai_compat.OpenAICompatProvider) and p.base_url.endswith("/api/v1")
    with pytest.raises(ValueError):
        make_provider("nope", "x")


def test_gemini_names_the_replacement_for_a_retired_model():
    from core.providers.gemini import GeminiProvider
    msg = "Gemini: model not found. This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.6-flash for the best experience."
    assert GeminiProvider.suggested_model(msg) == "gemini-3.6-flash"
    assert GeminiProvider.suggested_model("Gemini: model not found. nothing here") == ""


def test_gemini_reads_the_retry_delay_from_the_rate_limit_message():
    from core.providers.gemini import GeminiProvider
    assert GeminiProvider.retry_delay("quota exceeded ... Please retry in 12.5s.") == 12.5
    assert GeminiProvider.retry_delay('"retryDelay": "7s"') == 7.0
    assert GeminiProvider.retry_delay("no hint") == 0.0


def test_gemini_marks_injected_function_calls_for_signature_validation():
    from core.providers.gemini import GeminiProvider
    from core.schema import Message, ToolCall, ToolResult
    prov = GeminiProvider(model="gemini-3.5-flash-lite", api_key="x")
    msgs = [Message.user("tidy"), Message("assistant", [ToolCall("id1", "tidy_labels", {})]), Message.tool_results([ToolResult("id1", "tidy_labels", "{}")]),
            Message("assistant", [ToolCall("id2", "run_commands", {"commands": ["view"]}, {"thoughtSignature": "abc"})])]
    contents = prov._to_contents(msgs)
    calls = [p for c in contents for p in c["parts"] if "functionCall" in p]
    assert calls[0]["thoughtSignature"] == "skip_thought_signature_validator" and calls[1]["thoughtSignature"] == "abc"
