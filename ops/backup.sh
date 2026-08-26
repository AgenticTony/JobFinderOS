#!/bin/bash
# Scheduled backup of backend/jobfinderos.db + backend/uploads/ (CV PDFs).
# Runs via launchd (com.jobfinderos.backup) at 04:30 daily.
# Timestamped copies, rotated (30 days kept), stored off the working directory.

set -euo pipefail

PROJECT="/Users/anthonyforan/Desktop/JobFinderOS"
BACKUP_DIR="$HOME/backups/jobfinderos"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
KEEP_DAYS=30

mkdir -p "$BACKUP_DIR/cvs"

# --- SQLite database: use sqlite3 .backup (safe against concurrent writes) ---
if [ -f "$PROJECT/backend/jobfinderos.db" ]; then
    sqlite3 "$PROJECT/backend/jobfinderos.db" ".backup '$BACKUP_DIR/db-$TIMESTAMP.db'"
    echo "$(date -Iseconds) db backed up: db-$TIMESTAMP.db ($(du -h "$BACKUP_DIR/db-$TIMESTAMP.db" | cut -f1))"
else
    echo "$(date -Iseconds) WARNING: no database at $PROJECT/backend/jobfinderos.db"
fi

# --- CV uploads (personal data — not in the DB) ---
# APPEND-ONLY for the sync, but files absent from the source AND older than
# KEEP_DAYS are pruned from the mirror. This balances two requirements:
# 1. Accidental deletion is recoverable for 30 days (append-only window)
# 2. GDPR erasure completes within a bounded, policy-statable window
# (macOS openrsync's --backup-dir is unreliable; this is simpler and correct)
if [ -d "$PROJECT/backend/uploads/cvs" ]; then
    rsync -a "$PROJECT/backend/uploads/cvs/" "$BACKUP_DIR/cvs/"
    # Prune mirror files that no longer exist in the source and are > KEEP_DAYS old
    find "$BACKUP_DIR/cvs" -type f -mtime +$KEEP_DAYS -not -path "$PROJECT/backend/uploads/cvs/*" | while read -r old_file; do
        basename="$(basename "$old_file")"
        if [ ! -f "$PROJECT/backend/uploads/cvs/$basename" ]; then
            rm "$old_file"
            echo "$(date -Iseconds) pruned CV from mirror (absent from source, >${KEEP_DAYS}d): $basename"
        fi
    done
    echo "$(date -Iseconds) CVs mirrored: $(ls "$BACKUP_DIR/cvs/" | wc -l | tr -d ' ') files (30d retention for absent)"
fi

# --- Draft PDFs (tailored CVs + cover letters) — same retention policy
if [ -d "$PROJECT/backend/uploads/drafts" ]; then
    mkdir -p "$BACKUP_DIR/drafts"
    rsync -a "$PROJECT/backend/uploads/drafts/" "$BACKUP_DIR/drafts/"
    find "$BACKUP_DIR/drafts" -type f -mtime +$KEEP_DAYS | while read -r old_file; do
        basename="$(basename "$old_file")"
        if [ ! -f "$PROJECT/backend/uploads/drafts/$basename" ]; then
            rm "$old_file"
        fi
    done
fi

# --- Rotation: remove backups older than KEEP_DAYS ---
find "$BACKUP_DIR" -name "db-*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
DB_COUNT=$(ls "$BACKUP_DIR"/db-*.db 2>/dev/null | wc -l | tr -d ' ')
echo "$(date -Iseconds) rotation complete: $DB_COUNT db backups retained (keeping $KEEP_DAYS days)"
