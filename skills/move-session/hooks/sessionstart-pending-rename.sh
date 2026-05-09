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
#
# Two copy-pastable commands per marker:
#   - `/rename <new-tag>`: the model runs this inside CC if the current
#     resumed session matches the marker's UUID. Updates the picker label.
#   - `rm "<marker-path>"`: the USER runs this in a normal shell, OUTSIDE
#     CC, to clear the marker once the rename has happened. The global
#     bash-hard-deny hook blocks local-file `rm` from inside CC, so the
#     model cannot delete the marker itself - that is by design. Surfacing
#     the exact command keeps cleanup deterministic regardless of how long
#     elapses between the move and the next resume.
echo "Pending session-rename markers found (left by the move-session skill)."
echo ""
echo "If your current resumed session UUID matches one of the markers below,"
echo "run the /rename command to update the picker display label. The marker"
echo "file itself must be deleted from a normal shell OUTSIDE Claude Code"
echo "(bash-hard-deny blocks local-file rm from inside CC) - the rm command"
echo "is given for the user to copy-paste later. Both commands stay valid"
echo "until they are run, so it doesn't matter how long it takes."
echo ""
i=0
for marker in "${markers[@]}"; do
  i=$((i + 1))
  session_dir="$(dirname "$marker")"
  tag_from_dir="$(basename "$session_dir")"
  marker_uuid="$(grep '^uuid:' "$marker" 2>/dev/null | head -1 | awk '{print $2}')"
  marker_tag="$(grep '^tag:' "$marker" 2>/dev/null | head -1 | awk '{print $2}')"
  effective_tag="${marker_tag:-$tag_from_dir}"
  echo "  Marker $i:"
  echo "    Session dir: $session_dir"
  echo "    UUID:        ${marker_uuid:-unknown}"
  echo "    /rename command (run INSIDE CC if this is your session):"
  echo "      /rename $effective_tag"
  echo "    rm command (run in a normal shell OUTSIDE CC to clear this reminder):"
  echo "      rm \"$marker\""
  echo ""
done
echo "If you are resuming a different session in this project, you can ignore"
echo "the markers - they belong to other sessions."
