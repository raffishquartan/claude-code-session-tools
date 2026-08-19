#!/bin/bash
# SessionStart hook for the move-session skill.
#
# Scans the resumed session's project cwd for `cc-sessions/*/.pending-rename`
# markers (written by move_session.py on a tag-changing operation). When found,
# emits a system reminder telling the model to run `/rename <tag>` to fix the
# picker label and then delete the marker.
#
# CC supplies the project cwd in either CLAUDE_PROJECT_DIR (preferred) or via
# the json payload on stdin (cwd field). We try both.
#
# Three behaviours keep the scan cheap, bounded, and never silent:
#
#   - The scan root is canonicalised once (`cd -P`), so a project reached
#     through a symlink (~/cc/<project> -> a 9p-mounted Windows drive, say) is
#     walked by its real path instead of resolving the symlink hop for every
#     entry `find` touches. No-op on the common case of a real directory.
#   - The scan runs in the background against a soft deadline
#     (CCST_PENDING_RENAME_SOFT_TIMEOUT, whole seconds, default 5).
#     Overrunning it prints a short "could not complete" notice with the
#     manual command and exits 0. Without it, the harness kills the hook at
#     its own timeout and the user sees nothing at all - not even the
#     remediation block a found marker would have printed.
#   - Markers whose rename has already been applied (the session transcript
#     carries a `custom-title` equal to the marker's tag) are dropped from the
#     report: they are noise, not a to-do. The marker file itself is left
#     alone - deleting files stays a user action, run outside CC.
#
# Bash 3.2 compatible (macOS still ships 3.2): no mapfile, no `wait -n`, no
# associative arrays, no dependency on GNU `timeout`.

set -euo pipefail

# Wall-clock budget starts here, not at the wait: an external command that
# stalls during setup then eats its own delay instead of adding to the total.
SECONDS=0

# Whole seconds, and the registered hook timeout (10s in hooks-bundle.json)
# needs real margin above it, not the 2s a stopwatch would suggest: a bare
# `sleep 5` on the WSL2 box this was measured on returns after 7.94s in roughly
# one run in five - the VM parks an idle vCPU and every timer, builtin or not,
# inherits the slack. The margin has to cover that or the harness kills the
# hook before the notice prints, which is the whole bug being fixed.
soft_timeout="${CCST_PENDING_RENAME_SOFT_TIMEOUT:-5}"
# Above this many still-pending markers the per-marker block is replaced by a
# count plus the two bulk-clear commands; a long dump nobody reads is the
# reason these reminders get ignored in the first place.
detail_max=3

# Best-effort cwd resolution. CLAUDE_PROJECT_DIR is set by CC for SessionStart
# hooks; fall back to PWD if absent.
project_cwd="${CLAUDE_PROJECT_DIR:-${PWD:-}}"
if [[ -z "$project_cwd" || ! -d "$project_cwd" ]]; then
  exit 0
fi

# Canonicalise the one directory we are about to walk. `cd -P` + `pwd` is the
# portable idiom: `realpath` and `readlink -f` are absent or differently
# spelled on macOS.
if ! cc_sessions_dir="$(cd -P "$project_cwd/cc-sessions" 2>/dev/null && pwd)"; then
  exit 0
fi

# Does this marker's rename already show up in the session's transcript? If so
# the marker is stale and there is nothing for the user to do.
marker_fulfilled() {
  local marker="$1"
  local uuid tag transcript

  uuid="$(awk '/^uuid:/ {print $2; exit}' "$marker" 2>/dev/null || true)"
  tag="$(awk '/^tag:/ {print $2; exit}' "$marker" 2>/dev/null || true)"
  if [[ -z "$uuid" || -z "$tag" ]]; then
    return 1
  fi

  # The marker records the UUID but not which project directory holds the
  # transcript, so glob across all of them.
  for transcript in "$HOME"/.claude/projects/*/"$uuid".jsonl; do
    if [[ ! -f "$transcript" ]]; then
      continue
    fi
    if grep -qE "\"type\": ?\"custom-title\".*\"customTitle\": ?\"$tag\"|\"customTitle\": ?\"$tag\".*\"type\": ?\"custom-title\"" "$transcript" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

# Everything that touches the filesystem lives here, so the soft deadline
# covers the transcript reads as well as the `find` walk.
scan() {
  local marker
  find "$cc_sessions_dir" -maxdepth 2 -name ".pending-rename" -type f 2>/dev/null |
    while IFS= read -r marker; do
      if marker_fulfilled "$marker"; then
        continue
      fi
      printf '%s\n' "$marker"
    done
}

scan_out="$(mktemp "${TMPDIR:-/tmp}/pending-rename.XXXXXX")"
scan_fifo="$scan_out.fifo"
mkfifo "$scan_fifo"
trap 'rm -f "$scan_out" "$scan_fifo"' EXIT
# Opened read-write so the open does not block on a writer and the read below
# never sees EOF while the scan is still running.
exec 3<> "$scan_fifo"

# The scan announces completion down the fifo. The background group takes
# stdin/stdout/stderr away from this script's own descriptors, via `exec` so
# bash does not stash the originals on a spare fd to restore later: killing the
# group is not enough on its own, because bash defers a SIGTERM until the
# command it is waiting on returns, and anything still holding the hook's
# stdout keeps a caller that reads to EOF waiting for a hook that already
# exited - exactly the stall the soft deadline exists to prevent.
( exec > /dev/null 2>&1 < /dev/null; scan > "$scan_out" || true; printf 'done\n' >&3 ) &
scan_pid=$!

# `read -t` is the deadline: a shell builtin, so it cannot be delayed by the
# thing that makes this hook overrun in the first place. Both alternatives can
# be, and were measured being: a `sleep`-per-tick poll loop and a `sleep`-based
# watchdog child each drifted ~3s past the budget on a loaded WSL box, because
# spawning /bin/sleep itself stalled. macOS ships no GNU `timeout`, and `wait`
# has no timeout of its own. Whole seconds only - bash 3.2's `read -t` rejects
# fractions.
budget=$((soft_timeout - SECONDS))
if [[ $budget -lt 1 ]]; then
  budget=1
fi

if ! read -t "$budget" -r _ <&3; then
  # Only the immediate child is signalled; a `find` it spawned may outlive it
  # briefly, writing into a file we are about to delete. Harmless, and cheaper
  # than turning on job control just to signal a process group.
  kill "$scan_pid" 2>/dev/null || true
  echo "Pending session-rename check gave up after ${soft_timeout}s (slow filesystem?) - markers, if any, were not read."
  echo ""
  echo "To run the check by hand outside CC:"
  echo "    find \"$cc_sessions_dir\" -name .pending-rename    # this project"
  echo "    find -L ~/cc -name .pending-rename                 # all projects"
  exit 0
fi

# Portable read loop rather than `mapfile` (bash 4.0+ only) - this script's
# shebang binds to /bin/bash, which is bash 3.2 on macOS.
markers=()
while IFS= read -r marker; do
  markers+=("$marker")
done < "$scan_out"
if [[ ${#markers[@]} -eq 0 ]]; then
  exit 0
fi

if [[ ${#markers[@]} -gt $detail_max ]]; then
  echo "${#markers[@]} pending session-rename markers in this project (per-marker detail suppressed above ${detail_max}). Clear this project, or every project:"
  echo "    find \"$cc_sessions_dir\" -name .pending-rename -delete"
  echo "    find -L ~/cc -name .pending-rename -delete"
  exit 0
fi

# Build a single reminder block. The model reads this and acts on it.
echo "${#markers[@]} pending session-rename marker(s) in this project (left by the move-session skill)."
echo ""
echo "TO SILENCE ALL REMINDERS IN THIS PROJECT AT ONCE (quick option):"
echo "  Run this in a normal shell outside CC — deletes markers without updating picker labels:"
echo "    find \"$cc_sessions_dir\" -name .pending-rename -delete"
echo ""
echo "TO SILENCE ALL REMINDERS ACROSS ALL PROJECTS AT ONCE:"
echo "    find -L ~/cc -name .pending-rename -delete"
echo ""
echo "TO FIX AN INDIVIDUAL SESSION (updates picker label AND silences reminder):"
echo "  1. Resume the session:  cd <project-dir> && claude --resume <uuid>"
echo "  2. Run inside CC:       /rename <tag>  (shown per marker below)"
echo "  3. Run outside CC:      rm <marker-path>  (shown per marker below)"
echo ""
echo "--- Markers in this project ---"
echo ""
i=0
for marker in "${markers[@]}"; do
  i=$((i + 1))
  session_dir="$(dirname "$marker")"
  tag_from_dir="$(basename "$session_dir")"
  marker_uuid="$(awk '/^uuid:/ {print $2; exit}' "$marker" 2>/dev/null || true)"
  marker_tag="$(awk '/^tag:/ {print $2; exit}' "$marker" 2>/dev/null || true)"
  effective_tag="${marker_tag:-$tag_from_dir}"
  echo "  [$i] UUID: ${marker_uuid:-unknown}"
  echo "      Dir:  $session_dir"
  echo "      Inside CC:   /rename $effective_tag"
  echo "      Outside CC:  rm \"$marker\""
  echo ""
done
