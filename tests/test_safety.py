from core.safety import classify, needs_confirmation, split_commands, AUTONOMY_ASK, AUTONOMY_AUTO, AUTONOMY_ALL


def test_safe_commands():
    for cmd in ["color #1 red", "open 1abc", "open alphafold:P55085", "open emdb:1080",
                "select #1:159", "view", "roll y 0.5", "surface #1", "open https://x.org/a.pdb",
                "~cartoon #1", "hide solvent", "set bgColor white", "undo"]:
        assert not classify(cmd).confirm, cmd


def test_risky_commands():
    for cmd in ["close #1", "close", "delete solvent", "save ~/a.png", "exit", "quit",
                "runscript foo.py", "open /home/me/x.pdb", "open ~/Desktop/a.cxs", "open model.pdb",
                "toolshed install x", "pip install y", "remotecontrol rest start", "perframe turn y 1",
                "~delete #1", "swapaa #1:5 ala"]:
        assert classify(cmd).confirm, cmd
        assert classify(cmd).reason


def test_modes():
    cmds = ["color #1 red", "close #1"]
    assert [c.command for c in needs_confirmation(cmds, AUTONOMY_AUTO)] == ["close #1"]
    assert len(needs_confirmation(cmds, AUTONOMY_ASK)) == 2
    assert needs_confirmation(cmds, AUTONOMY_ALL) == []


def test_split():
    assert split_commands("open 1abc; color red ;; # comment") == ["open 1abc", "color red"]


def test_abbreviations_and_remote_scripts_need_confirmation():
    for cmd in ["clo #1", "del solvent", "sav x.png", "exi", "open https://example.org/evil.py",
                "open https://example.org/run.cxc", "log save ~/log.html", "movie record", "snapshot"]:
        assert classify(cmd).confirm, cmd
    for cmd in ["open https://files.rcsb.org/download/4hhb.cif", "open https://x.org/map.mrc.gz", "log clear", "select #1"]:
        assert not classify(cmd).confirm, cmd


def test_compressed_scripts_and_write_options():
    assert classify("open https://x.org/evil.py.gz").confirm
    assert classify("open local.cxc.gz").confirm
    assert classify("hbonds #1 saveFile /tmp/h.txt").confirm
    assert not classify("hbonds #1 reveal true").confirm
    assert not classify("open https://files.rcsb.org/download/4hhb.cif.gz").confirm
