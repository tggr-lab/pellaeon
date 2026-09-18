from core.schema import (Message, TextPart, ToolCall, ToolResult, OpaquePart,
                         conversation_to_json, conversation_from_json, estimate_tokens)


def test_roundtrip_json():
    msgs = [
        Message.user("hello", context="<state/>"),
        Message("assistant", [OpaquePart("anthropic", {"type": "thinking", "thinking": "", "signature": "x"}),
                              TextPart("hi"), ToolCall("c1", "run_commands", {"commands": ["open 1abc"]})]),
        Message.tool_results([ToolResult("c1", "run_commands", "ok", False)]),
    ]
    back = conversation_from_json(conversation_to_json(msgs))
    assert [m.role for m in back] == ["user", "assistant", "tool"]
    assert back[0].meta["context"] == "<state/>"
    assert back[1].tool_calls()[0].args == {"commands": ["open 1abc"]}
    assert isinstance(back[1].parts[0], OpaquePart)
    assert back[2].tool_results_list()[0].call_id == "c1"


def test_helpers():
    m = Message("assistant", [TextPart("a"), TextPart("b"), ToolCall("1", "get_state", {})])
    assert m.text() == "ab"
    assert m.has_tool_calls()
    assert estimate_tokens("x" * 40) == 10
