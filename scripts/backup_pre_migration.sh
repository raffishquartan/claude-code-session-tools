#!/usr/bin/env bash
# scripts/backup_pre_migration.sh
#
# One-shot, read-only snapshot of every pre-0.19.0 flat-file/JSONL/TOML data-store
# location this repo's SQLite migration touches, taken BEFORE any of the
# migrate_*.py scripts run. This is independent of (and in addition to) each
# migration script's own internal tar-backup — it captures everything in one
# archive, before anything has been touched, so a full rollback or inspection
# never requires hunting through four separate migration-backups/*.tar.gz files.
#
# Touches nothing under ~/.claude/, ~/.cache/claude/, ~/.cache/cc-session-tools/,
# or ~/.local/share/claude/ — every operation below is a read (cp/tar), never a
# write or delete against those trees. Safe to run any number of times; each run
# writes a new, separately timestamped archive.
#
# Usage: bash scripts/backup_pre_migration.sh [output-dir]
#   output-dir defaults to $HOME (so the archive is easy to find, and is
#   deliberately NOT under any of the directories this script reads from).

set -euo pipefail

OUT_DIR="${1:-$HOME}"
STAMP="$(date +%Y%m%dT%H%M%SZ)"
STAGING="$(mktemp -d)"
ARCHIVE="${OUT_DIR%/}/claude-data-store-migration-backup-${STAMP}.tar.gz"

cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

echo "Staging pre-migration snapshot in ${STAGING} ..."

copy_if_exists() {
    # copy_if_exists <src> <dest-subdir-under-staging>
    local src="$1" dest="${STAGING}/$2"
    if [ -e "$src" ]; then
        mkdir -p "$(dirname "$dest")"
        cp -a "$src" "$dest"
        echo "  captured: $src"
    else
        echo "  (not present, skipping) $src"
    fi
}

echo
echo "== ccmsg (~/.claude/cc-messages/) =="
copy_if_exists "$HOME/.claude/cc-messages" "ccmsg/cc-messages"

echo
echo "== ccsched (~/.claude/cc-scheduler/) =="
copy_if_exists "$HOME/.claude/cc-scheduler" "ccsched/cc-scheduler"

echo
echo "== sessions: tag cache + doctor-mutes =="
copy_if_exists "$HOME/.cache/claude/session-tags" "sessions/session-tags"
copy_if_exists "$HOME/.claude/cc-doctor-mutes.json" "sessions/cc-doctor-mutes.json"
echo "  NOTE: per-session .last-opened/.last-active sentinel files under each"
echo "  project's cc-sessions/<basename>/ are intentionally left in place by the"
echo "  sessions.db migration (never deleted) - not captured here, nothing to"
echo "  restore for them."

echo
echo "== telemetry (~/.cache/claude/logs/fires.jsonl*) =="
copy_if_exists "$HOME/.cache/claude/logs/fires.jsonl" "telemetry/fires.jsonl"
for n in 1 2 3; do
    copy_if_exists "$HOME/.cache/claude/logs/fires.jsonl.$n" "telemetry/fires.jsonl.$n"
done

echo
echo "== command-cache (path move only - not auto-deleted by any migration script) =="
copy_if_exists "$HOME/.cache/claude/logs/command-cache.db" "command-cache/command-cache.db"

echo
echo "== claude-flags cache (path move only - not auto-deleted by any migration script) =="
copy_if_exists "$HOME/.cache/cc-session-tools/claude-flags.json" "claude-flags/claude-flags.json"

echo
echo "Writing archive: ${ARCHIVE}"
tar czf "$ARCHIVE" -C "$STAGING" .

echo
echo "Done. ${ARCHIVE}"
echo "This archive is independent of each migration script's own"
echo "~/.local/share/claude/migration-backups/*.tar.gz - keep both until the"
echo "audit checklist in docs/data-store-migration-backups.md passes and the"
echo "30-day retention window has elapsed."
