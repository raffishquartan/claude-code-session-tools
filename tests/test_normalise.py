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
    # /usr/bin/git → basename 'git' → git rule applies
    assert normalise("/usr/bin/git status") == "git status <ARGS>"

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


# ---------------------------------------------------------------------------
# Git rule tests (Task 2)
# ---------------------------------------------------------------------------

def test_git_checkout_normalises_branch():
    assert normalise("git checkout feature/my-branch") == "git checkout <ARGS>"

def test_git_checkout_normalises_sha():
    assert normalise("git checkout abc1234") == "git checkout <ARGS>"

def test_git_checkout_main_normalises():
    assert normalise("git checkout main") == "git checkout <ARGS>"

def test_git_reset_hard_returns_none():
    assert normalise("git reset --hard") is None

def test_git_reset_hard_with_ref_returns_none():
    assert normalise("git reset --hard HEAD~1") is None

def test_git_push_force_returns_none():
    assert normalise("git push --force") is None
    assert normalise("git push -f") is None

def test_git_push_force_with_remote_returns_none():
    assert normalise("git push origin main --force") is None

def test_git_clean_returns_none():
    assert normalise("git clean -fd") is None

def test_git_diff_normalises():
    assert normalise("git diff HEAD~3..HEAD") == "git diff <ARGS>"

def test_git_log_normalises():
    assert normalise("git log --oneline -10 main") == "git log <ARGS>"

def test_git_config_returns_none():
    assert normalise("git config --get user.email") is None

def test_git_unknown_subcommand_returns_none():
    assert normalise("git bisect start") is None


# ---------------------------------------------------------------------------
# find rule tests (Task 2)
# ---------------------------------------------------------------------------

def test_find_basic_normalises():
    # -7 starts with '-' so it is kept verbatim (flag passthrough)
    assert normalise('find . -name "*.py" -mtime -7') == "find <PATH> -name <GLOB> -mtime -7"

def test_find_with_exec_returns_none():
    assert normalise('find . -name "*.py" -exec cat {} \\;') is None

def test_find_with_delete_returns_none():
    assert normalise('find /tmp -name "*.log" -delete') is None

def test_find_maxdepth_normalises():
    # 3 has no leading '-' so _classify_token returns <NUM>
    assert normalise("find . -maxdepth 3 -type f") == "find <PATH> -maxdepth <NUM> -type f"

def test_find_multiple_paths_all_collapsed():
    assert normalise("find /var/log /tmp -name '*.log'") == "find <PATH> <PATH> -name <GLOB>"


# ---------------------------------------------------------------------------
# Package manager rule tests (Task 2)
# ---------------------------------------------------------------------------

def test_npm_install_normalises():
    assert normalise("npm install lodash") == "npm install <ARGS>"

def test_npm_ci_normalises():
    assert normalise("npm ci") == "npm ci <ARGS>"

def test_npm_build_normalises():
    assert normalise("npm build") == "npm build <ARGS>"

def test_npm_test_normalises():
    assert normalise("npm test") == "npm test <ARGS>"

def test_npm_run_returns_none():
    assert normalise("npm run build") is None
    assert normalise("npm run exfiltrate-keys") is None

def test_npm_start_returns_none():
    assert normalise("npm start") is None

def test_npm_uninstall_returns_none():
    assert normalise("npm uninstall lodash") is None

def test_pip_install_normalises():
    assert normalise("pip3 install requests==2.31.0") == "pip3 install <ARGS>"

def test_cargo_build_normalises():
    assert normalise("cargo build") == "cargo build <ARGS>"

def test_pip_install_normalises():
    assert normalise("pip install requests") == "pip install <ARGS>"

def test_git_reset_soft_normalises():
    assert normalise("git reset HEAD~1") == "git reset <ARGS>"
    assert normalise("git reset --soft HEAD~1") == "git reset <ARGS>"

def test_git_reset_hard_still_blocked():
    # --hard is in _GIT_DANGEROUS_FLAGS — dangerous-flag guard fires before safe-subcmd check
    assert normalise("git reset --hard HEAD~1") is None

def test_git_bare_returns_none():
    assert normalise("git") is None

def test_find_relative_path_collapses():
    assert normalise("find log -name '*.txt'") == "find <PATH> -name <GLOB>"
