from core import prompt


def test_system_prompt_sections():
    sp = prompt.build_system_prompt(directory=[("color", "Colors things"), ("select", "Selects")],
                                    gotchas="Gotchas: x", recipes=[{"request": "spin", "commands": ["roll y 0.5"]}],
                                    allow_python=True)
    assert "You are Pellaeon" in sp
    assert "- color: Colors things" in sp
    assert "User: spin\nCommands: roll y 0.5" in sp
    assert "Gotchas: x" in sp
    assert "run_python" in sp


def test_context_and_state():
    state = {"models": [{"id": "#1", "name": "P55085", "type": "AtomicStructure", "num_residues": 397,
                         "chains": [{"id": "A", "range": "1-397"}]}],
             "selection": {"num_atoms": 10, "num_residues": 2, "spec": "#1:159,300"}, "background": "black"}
    ctx = prompt.build_context(state, [{"title": "color", "section": "usage", "text": "Usage: color spec"}])
    assert "#1 P55085 [AtomicStructure; 397 residues; chains A(1-397)]" in ctx
    assert "Selection: 10 atoms, 2 residues (#1:159,300)" in ctx
    assert "<docs>" in ctx and "Usage: color spec" in ctx
    assert "Nothing is open." in prompt.build_context({}, [])
