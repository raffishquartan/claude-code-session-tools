#!/usr/bin/env bats
# Bats tests for the SessionStart pending-rename hook.
# Run with: bats src/cc_session_tools/skills/move-session/tests/test_hook.bats
#
# The hook under test is the one next to this file, not the installed copy in
# ~/.claude/skills/ - otherwise a worktree's tests silently grade whichever
# build happens to be installed globally.

HOOK="$BATS_TEST_DIRNAME/../hooks/sessionstart-pending-rename.sh"

setup() {
    TMPHOME="$(mktemp -d)"
    PROJECT="$TMPHOME/project"
    mkdir -p "$PROJECT/cc-sessions"
}

teardown() {
    [[ -n "${TMPHOME:-}" && -d "$TMPHOME" ]] && rm -rf "$TMPHOME"
}

# Write a marker for <tag> under <project>/cc-sessions/<tag>/ with <uuid>.
write_marker() {
    local tag="$1" uuid="$2"
    mkdir -p "$PROJECT/cc-sessions/$tag"
    printf 'uuid: %s\ntag: %s\nwritten_at: 2026-05-03T14:00:00Z\n' \
        "$uuid" "$tag" > "$PROJECT/cc-sessions/$tag/.pending-rename"
}

# Write a transcript for <uuid> in the fake HOME whose custom-title is <title>.
write_transcript() {
    local uuid="$1" title="$2"
    local dir="$TMPHOME/.claude/projects/-fake-project"
    mkdir -p "$dir"
    printf '{"type":"custom-title","customTitle":"%s","sessionId":"%s"}\n' \
        "$title" "$uuid" > "$dir/$uuid.jsonl"
}

# A `find` earlier on PATH that stalls for <seconds> before delegating to the
# real one - the only way to exercise the deadline without a genuinely slow
# filesystem.
stub_slow_find() {
    local seconds="$1"
    local real_find
    real_find="$(command -v find)"
    mkdir -p "$TMPHOME/bin"
    printf '#!/bin/sh\nsleep %s\nexec %s "$@"\n' "$seconds" "$real_find" > "$TMPHOME/bin/find"
    chmod +x "$TMPHOME/bin/find"
}

@test "exits 0 with no output when no markers exist" {
    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "exits 0 with no output when project dir does not exist" {
    run env CLAUDE_PROJECT_DIR="/nonexistent/path" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "exits 0 with no output when cc-sessions dir does not exist" {
    BARE="$(mktemp -d)"
    run env CLAUDE_PROJECT_DIR="$BARE" "$HOOK" <<<'{}'
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
    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"pending session-rename marker(s)"* ]]
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

    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
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
    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    # The hook explains the split: /rename inside CC, rm outside CC, both
    # remain valid until run.
    [[ "$output" == *"Inside CC:"* ]]
    [[ "$output" == *"Outside CC:"* ]]
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
    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    # The /rename command must use the tag from the file, not the dir name.
    [[ "$output" == *"/rename 20260503-correct-tag-from-file"* ]]
    [[ "$output" != *"/rename some-old-name"* ]]
}

@test "cross-project remedy command uses -L so it descends through a symlinked ~/cc" {
    # ~/cc is commonly a symlink to the real projects root (e.g. an OneDrive
    # sync target). GNU find does not follow a symlink given as the search
    # root unless -L is passed, so the printed command must include it or
    # running it silently deletes nothing while claiming to have cleared
    # every reminder.
    SESSION="$PROJECT/cc-sessions/20260503-test-tag"
    mkdir -p "$SESSION"
    echo "uuid: aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb" > "$SESSION/.pending-rename"
    echo "tag: 20260503-test-tag" >> "$SESSION/.pending-rename"

    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"find -L ~/cc -name .pending-rename -delete"* ]]
}

@test "ignores markers nested deeper than 2 levels" {
    # Hook uses -maxdepth 2 (cc-sessions/<dir>/.pending-rename); deeper should not surface.
    deep="$PROJECT/cc-sessions/some-dir/nested-deeper"
    mkdir -p "$deep"
    echo "uuid: deep" > "$deep/.pending-rename"
    run env CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "canonicalises the scan root so a symlinked project reports its real path" {
    # ~/cc/<project> is a symlink onto a 9p-mounted Windows drive on the
    # author's machine; walking through the link costs a resolution per entry
    # `find` touches. Resolving once up front keeps every reported path - and
    # the walk itself - on the real filesystem.
    real="$TMPHOME/real-project"
    mkdir -p "$real/cc-sessions/20260503-via-symlink"
    printf 'uuid: 11111111-2222-3333-4444-555555555555\ntag: 20260503-via-symlink\n' \
        > "$real/cc-sessions/20260503-via-symlink/.pending-rename"
    ln -s "$real" "$TMPHOME/linked-project"
    canonical="$(cd -P "$real" && pwd)"

    run env HOME="$TMPHOME" CLAUDE_PROJECT_DIR="$TMPHOME/linked-project" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"$canonical/cc-sessions/20260503-via-symlink"* ]]
    [[ "$output" != *"linked-project"* ]]
}

@test "prints a fallback notice when the scan overruns the soft deadline" {
    # The whole point: the harness kills an overrunning hook with zero output,
    # so the hook must beat it to the punch and always say something.
    write_marker "20260503-slow" "22222222-2222-3333-4444-555555555555"
    stub_slow_find 3

    run env PATH="$TMPHOME/bin:$PATH" HOME="$TMPHOME" \
        CCST_PENDING_RENAME_SOFT_TIMEOUT=1 CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"gave up after 1s"* ]]
    # ...and tells the user how to run the check by hand.
    [[ "$output" == *"find -L ~/cc -name .pending-rename"* ]]
}

@test "returns inside the registered timeout when the scan hangs" {
    # Registered hard timeout is 10s (hooks-bundle.json); the default 5s soft
    # deadline must leave headroom for the notice to print and the script to
    # exit - including the ~3s of timer slack a loaded WSL2 box adds to any
    # 5s wait, builtin or otherwise.
    write_marker "20260503-hung" "33333333-2222-3333-4444-555555555555"
    stub_slow_find 30

    start="$(date +%s)"
    run env PATH="$TMPHOME/bin:$PATH" HOME="$TMPHOME" \
        CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    elapsed=$(( $(date +%s) - start ))
    [ "$status" -eq 0 ]
    [[ "$output" == *"gave up after 5s"* ]]
    [ "$elapsed" -lt 10 ]
}

@test "drops a marker whose rename has already been applied" {
    # B-001: once the transcript's custom-title equals the marker's tag the
    # rename has happened and the marker is stale noise, not a to-do.
    write_marker "20260503-already-renamed" "44444444-2222-3333-4444-555555555555"
    write_transcript "44444444-2222-3333-4444-555555555555" "20260503-already-renamed"

    run env HOME="$TMPHOME" CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    # The marker file itself is left alone - deleting files stays a user action.
    [ -f "$PROJECT/cc-sessions/20260503-already-renamed/.pending-rename" ]
}

@test "keeps a marker whose transcript still carries the old title" {
    write_marker "20260503-new-tag" "55555555-2222-3333-4444-555555555555"
    write_transcript "55555555-2222-3333-4444-555555555555" "20260503-old-tag"

    run env HOME="$TMPHOME" CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"/rename 20260503-new-tag"* ]]
}

@test "collapses to a count plus bulk-clear commands above 3 pending markers" {
    for n in 1 2 3 4; do
        write_marker "2026050$n-many" "6666666$n-2222-3333-4444-555555555555"
    done

    run env HOME="$TMPHOME" CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"4 pending session-rename markers"* ]]
    [[ "$output" == *"find -L ~/cc -name .pending-rename -delete"* ]]
    [[ "$output" == *"-name .pending-rename -delete"* ]]
    # No per-marker dump at this size.
    [[ "$output" != *"Inside CC:"* ]]
    [[ "$output" != *"/rename"* ]]
}

@test "keeps the full per-marker detail at 3 markers" {
    for n in 1 2 3; do
        write_marker "2026050$n-few" "7777777$n-2222-3333-4444-555555555555"
    done

    run env HOME="$TMPHOME" CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"/rename 20260501-few"* ]]
    [[ "$output" == *"/rename 20260502-few"* ]]
    [[ "$output" == *"/rename 20260503-few"* ]]
    [[ "$output" == *"Inside CC:"* ]]
}

@test "counts only still-pending markers when deciding to collapse" {
    # 5 markers, 2 already renamed -> 3 remain, so the full detail is kept.
    for n in 1 2 3 4 5; do
        write_marker "2026050$n-mixed" "8888888$n-2222-3333-4444-555555555555"
    done
    write_transcript "88888884-2222-3333-4444-555555555555" "20260504-mixed"
    write_transcript "88888885-2222-3333-4444-555555555555" "20260505-mixed"

    run env HOME="$TMPHOME" CLAUDE_PROJECT_DIR="$PROJECT" "$HOOK" <<<'{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"3 pending session-rename marker(s)"* ]]
    [[ "$output" == *"/rename 20260501-mixed"* ]]
    [[ "$output" != *"/rename 20260504-mixed"* ]]
    [[ "$output" != *"/rename 20260505-mixed"* ]]
}
