#!/bin/bash
# Scheduled backup of backend/jobfinderos.db + backend/uploads/ (CV PDFs).
# Runs via launchd (com.jobfinderos.backup) at 04:30 daily.
# Timestamped copies, rotated (30 days kept), stored off the working directory.

set -euo pipefail

PROJECT="/Users/anthonyforan/Desktop/JobFinderOS"
BACKUP_DIR="$HOME/backups/jobfinderos"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
KEEP_DAYS=30

mkdir -p "$BACKUP_DIR/cvs" "$BACKUP_DIR/drafts"

# --- SQLite database: use sqlite3 .backup (safe against concurrent writes) ---
if [ -f "$PROJECT/backend/jobfinderos.db" ]; then
    sqlite3 "$PROJECT/backend/jobfinderos.db" ".backup '$BACKUP_DIR/db-$TIMESTAMP.db'"
    echo "$(date -Iseconds) db backed up: db-$TIMESTAMP.db ($(du -h "$BACKUP_DIR/db-$TIMESTAMP.db" | cut -f1))"
else
    echo "$(date -Iseconds) WARNING: no database at $PROJECT/backend/jobfinderos.db"
fi

# --- CV uploads: sync + absence-date-based retention ---
# The retention window must be measured from when a file was FIRST DETECTED
# ABSENT from the source, not from its mtime (rsync -a preserves source
# mtime, so a 60-day-old CV deleted today would be pruned immediately —
# zero recovery window). We track absence detection by touching a marker
# file on first detection, then prune only when the marker is > KEEP_DAYS.
sync_with_absence_retention() {
    local src_dir="$1" mirror_dir="$2" label="$2"
    local pending_dir="$mirror_dir.pending-delete"
    mkdir -p "$mirror_dir" "$pending_dir"

    # Sync (append-only: no --delete, so nothing is removed during sync)
    rsync -a "$src_dir/" "$mirror_dir/"

    # Detect absent files and create dated markers on first observation
    for mirror_file in "$mirror_dir"/*; do
        [ -f "$mirror_file" ] || continue
        local basename
        basename="$(basename "$mirror_file")"
        if [ ! -f "$src_dir/$basename" ]; then
            # File is absent from source — create or update its absence marker
            local marker="$pending_dir/$basename"
            if [ ! -f "$marker" ]; then
                touch "$marker"
                echo "$(date -Iseconds) detected absence: $basename (deletion pending, $KEEP_DAYS-day window)"
            fi
        else
            # File exists in source — remove any stale absence marker
            rm -f "$pending_dir/$basename"
        fi
    done

    # Prune files whose absence markers are old enough
    find "$pending_dir" -type f -mtime +$KEEP_DAYS | while read -r marker; do
        local basename
        basename="$(basename "$marker")"
        rm -f "$mirror_dir/$basename" "$marker"
        echo "$(date -Iseconds) pruned from mirror (absent >${KEEP_DAYS}d): $basename"
    done
}

if [ -d "$PROJECT/backend/uploads/cvs" ]; then
    sync_with_absence_retention "$PROJECT/backend/uploads/cvs" "$BACKUP_DIR/cvs"
    echo "$(date -Iseconds) CVs: $(ls "$BACKUP_DIR/cvs/" | wc -l | tr -d ' ') in mirror"
fi

if [ -d "$PROJECT/backend/uploads/drafts" ]; then
    sync_with_absence_retention "$PROJECT/backend/uploads/drafts" "$BACKUP_DIR/drafts"
fi

# --- Rotation: remove database backups older than KEEP_DAYS ---
find "$BACKUP_DIR" -maxdepth 1 -name "db-*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
DB_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name "db-*.db" | wc -l | tr -d ' ')
echo "$(date -Iseconds) rotation complete: $DB_COUNT db backups retained (keeping $KEEP_DAYS days)"
