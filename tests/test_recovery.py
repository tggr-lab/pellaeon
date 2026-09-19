from core.recovery import parse_textual_tool_call, parse_command_lines


def test_textual_tool_calls_in_the_shapes_small_models_use():
    assert parse_textual_tool_call('run_commands "color #1 white ; select #1:10 ; show sel atoms"') == ["color #1 white", "select #1:10", "show sel atoms"]
    assert parse_textual_tool_call("run_commands hide /A targ acs") == ["hide /A targ acs"]
    assert parse_textual_tool_call('run_commands ["hbonds log true"]') == ["hbonds log true"]
    assert parse_textual_tool_call('run_commands{commands:[<|"|>color helix red; color strand yellow<|"|>]}') == ["color helix red", "color strand yellow"]
    assert parse_textual_tool_call('<tool_call>{"name": "run_commands", "arguments": {"commands": ["set bgColor white", "view"]}}</tool_call>') == ["set bgColor white", "view"]
    assert parse_textual_tool_call("Run the following:\n```\ncolor #1 red\nview\n```") == ["color #1 red", "view"]


def test_prose_and_unknown_words_are_not_commands():
    assert parse_textual_tool_call("The chains are colored according to their IDs.") == []
    assert parse_textual_tool_call('run_commands "make it pretty please"') == []
    assert parse_textual_tool_call("```\nimport os\nos.system('rm -rf /')\n```") == []


def test_command_lines_from_a_commands_only_reply():
    text = "1. `color #1 bychain`\n- show ligand atoms; style ligand sphere\nRun: set bgColor white\nThat should do it."
    assert parse_command_lines(text) == ["color #1 bychain", "show ligand atoms", "style ligand sphere", "set bgColor white"]
