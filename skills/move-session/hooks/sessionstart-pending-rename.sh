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

set -euo pipefail

# Best-effort cwd resolution. CLAUDE_PROJECT_DIR is set by CC for SessionStart
# hooks; fall back to PWD if absent.
project_cwd="${CLAUDE_PROJECT_DIR:-${PWD:-}}"
if [[ -z "$project_cwd" || ! -d "$project_cwd" ]]; then
  exit 0
fi

cc_sessions_dir="$project_cwd/cc-sessions"
if [[ ! -d "$cc_sessions_dir" ]]; then
  exit 0
fi

# Collect all .pending-rename markers in this project's cc-sessions/.
mapfile -t markers < <(find "$cc_sessions_dir" -maxdepth 2 -name ".pending-rename" -type f 2>/dev/null)
if [[ ${#markers[@]} -eq 0 ]]; then
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
  marker_uuid="$(grep '^uuid:' "$marker" 2>/dev/null | head -1 | awk '{print $2}')"
  marker_tag="$(grep '^tag:' "$marker" 2>/dev/null | head -1 | awk '{print $2}')"
  effective_tag="${marker_tag:-$tag_from_dir}"
  echo "  [$i] UUID: ${marker_uuid:-unknown}"
  echo "      Dir:  $session_dir"
  echo "      Inside CC:   /rename $effective_tag"
  echo "      Outside CC:  rm \"$marker\""
  echo ""
done
