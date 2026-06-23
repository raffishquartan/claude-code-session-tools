from __future__ import annotations

from cccs_hooks.normalise import _classify_token, normalise

# _classify_token tests

def test_classify_uuid():
    assert _classify_token("abc12345-1234-1234-1234-abcdef012345") == "<UUID>"

def test_classify_date_iso():
    assert _classify_token("2026-06-23") == "<DATE>"

def test_classify_date_compact():
    assert _classify_token("20260623") == "<DATE>"

def test_classify_8digit_integer_returns_date():
    # 8-digit integers are classified as <DATE> (not <NUM>) because in shell
    # args they are overwhelmingly date-strings (YYYYMMDD) like 20260623.
    assert _classify_token("12345678") == "<DATE>"

def test_classify_number():
    assert _classify_token("42") == "<NUM>"

def test_classify_url():
    assert _classify_token("https://github.com/foo") == "<URL>"

def test_classify_glob():
    assert _classify_token("*.py") == "<GLOB>"
    assert _classify_token("**/*.ts") == "<GLOB>"

def test_classify_flag_passthrough():
    assert _classify_token("--name") == "--name"
    assert _classify_token("-r") == "-r"
    assert _classify_token("-7") == "-7"

def test_classify_word_passthrough():
    assert _classify_token("status") == "status"

# normalise() tests

def test_python3_returns_none():
    assert normalise("python3 script.py") is None

def test_bash_returns_none():
    assert normalise("bash deploy.sh") is None

def test_node_returns_none():
    assert normalise("node index.js") is None

def test_unparseable_command_returns_none():
    assert normalise("git checkout 'unclosed") is None

def test_absolute_path_verb_git():
    # basename resolution — git rules come in Task 2, so still None for now
    # but the verb IS resolved correctly (no crash)
    result = normalise("/usr/bin/git status")
    assert result is None  # git rule not yet implemented

def test_local_script_returns_none():
    assert normalise("./local-script.py") is None

def test_cat_normalises_path():
    assert normalise("cat /home/alice/file.txt") == "cat <PATHS>"

def test_head_normalises():
    assert normalise("head -n 20 some/file.py") == "head -n <NUM> <PATHS>"

def test_ls_normalises():
    assert normalise("ls -la /some/path") == "ls -la <PATHS>"

def test_wc_normalises():
    assert normalise("wc -l file.txt") == "wc -l <PATHS>"
