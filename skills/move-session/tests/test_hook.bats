#!/usr/bin/env bats
# Bats tests for the SessionStart pending-rename hook.
# Run with: bats ~/.claude/skills/move-session/tests/test_hook.bats

HOOK="$HOME/.claude/skills/move-session/hooks/sessionstart-pending-rename.sh"

setup() {
    TMPHOME="$(mktemp -d)"
    PROJECT="$TMPHOME/project"
    mkdir -p "$PROJECT/cc-sessions"
}

teardown() {
    [[ -n "${TMPHOME:-}" && -d "$TMPHOME" ]] && rm -rf "$TMPHOME"
}

@test "exits 0 with no output when no markers exist" {
    run env CLAUDE_PROJECT_DIR="$PROJECT" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "exits 0 with no output when project dir does not exist" {
    run env CLAUDE_PROJECT_DIR="/nonexistent/path" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "exits 0 with no output when cc-sessions dir does not exist" {
    BARE="$(mktemp -d)"
    run env CLAUDE_PROJECT_DIR="$BARE" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    rm -rf "$BARE"
}

@test "surfaces a single marker with uuid and tag" {
    SESSION="$PROJECT/cc-sessions/20260503-test-tag"
    mkdir -p "$SESSION"
    cat > "$SESSION/.pending-rename" <<EOF
uuid: aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb
tag: 20260503-test-tag
written_at: 2026-05-03T14:00:00Z
EOF
    run env CLAUDE_PROJECT_DIR="$PROJECT" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"Pending session-rename markers found"* ]]
    [[ "$output" == *"$SESSION"* ]]
    [[ "$output" == *"aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"* ]]
    [[ "$output" == *"20260503-test-tag"* ]]
}

@test "surfaces multiple markers" {
    mkdir -p "$PROJECT/cc-sessions/20260501-one"
    mkdir -p "$PROJECT/cc-sessions/20260502-two"
    echo "uuid: u1" > "$PROJECT/cc-sessions/20260501-one/.pending-rename"
    echo "tag: 20260501-one" >> "$PROJECT/cc-sessions/20260501-one/.pending-rename"
    echo "uuid: u2" > "$PROJECT/cc-sessions/20260502-two/.pending-rename"
    echo "tag: 20260502-two" >> "$PROJECT/cc-sessions/20260502-two/.pending-rename"

    run env CLAUDE_PROJECT_DIR="$PROJECT" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"u1"* ]]
    [[ "$output" == *"u2"* ]]
    [[ "$output" == *"20260501-one"* ]]
    [[ "$output" == *"20260502-two"* ]]
}

@test "emits copy-pastable /rename and rm commands per marker" {
    # Item 7.2: instead of telling the model to delete the marker (which
    # bash-hard-deny blocks from inside CC), the hook prints both
    #   - a /rename command for the model to run inside CC, and
    #   - an rm command for the user to run outside CC
    # so cleanup is deterministic regardless of when the next resume happens.
    SESSION="$PROJECT/cc-sessions/20260503-renamed-tag"
    mkdir -p "$SESSION"
    cat > "$SESSION/.pending-rename" <<EOF
uuid: cccccccc-1111-2222-3333-dddddddddddd
tag: 20260503-renamed-tag
written_at: 2026-05-03T14:00:00Z
EOF
    run env CLAUDE_PROJECT_DIR="$PROJECT" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    # The hook explains the split: /rename inside CC, rm outside CC, both
    # remain valid until run.
    [[ "$output" == *"INSIDE Claude Code"* || "$output" == *"INSIDE CC"* ]]
    [[ "$output" == *"OUTSIDE Claude Code"* || "$output" == *"OUTSIDE CC"* ]]
    # Exact /rename command for this marker.
    [[ "$output" == *"/rename 20260503-renamed-tag"* ]]
    # Exact rm command for this marker - quoted, with the full marker path.
    [[ "$output" == *"rm \"$SESSION/.pending-rename\""* ]]
}

@test "uses tag from marker file even when dir name differs" {
    # If the cc-sessions directory has been manually renamed without /rename
    # being run, the marker's `tag:` field is still authoritative for the
    # /rename command (the dir name is the new tag the model is meant to
    # surface in the picker).
    SESSION="$PROJECT/cc-sessions/some-old-name"
    mkdir -p "$SESSION"
    cat > "$SESSION/.pending-rename" <<EOF
uuid: eeeeeeee-1111-2222-3333-ffffffffffff
tag: 20260503-correct-tag-from-file
written_at: 2026-05-03T14:00:00Z
EOF
    run env CLAUDE_PROJECT_DIR="$PROJECT" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    # The /rename command must use the tag from the file, not the dir name.
    [[ "$output" == *"/rename 20260503-correct-tag-from-file"* ]]
    [[ "$output" != *"/rename some-old-name"* ]]
}

@test "ignores markers nested deeper than 2 levels" {
    # Hook uses -maxdepth 2 (cc-sessions/<dir>/.pending-rename); deeper should not surface.
    deep="$PROJECT/cc-sessions/some-dir/nested-deeper"
    mkdir -p "$deep"
    echo "uuid: deep" > "$deep/.pending-rename"
    run env CLAUDE_PROJECT_DIR="$PROJECT" bash "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}
