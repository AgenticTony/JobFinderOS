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
    local src_dir="$1" mirror_dir="$2"
    local pending_dir="$mirror_dir.pending-delete"
    mkdir -p "$mirror_dir" "$pending_dir"

    # Sync (append-only: no --delete, so nothing is removed during sync)
    rsync -a "$src_dir/" "$mirror_dir/"

    # SANITY THRESHOLD: if the source is empty or nearly empty, something is
    # wrong (failed restore, storage bug, botched deploy) — do NOT mark the
    # entire mirror for deletion. An empty source is a plausible error state,
    # not necessarily intent. Refuse and require explicit override.
    local mirror_count source_count
    mirror_count=$(find "$mirror_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
    source_count=$(find "$src_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
    if [ "$mirror_count" -gt 0 ] && [ "$source_count" -eq 0 ]; then
        echo "$(date -Iseconds) WARNING: source $src_dir is EMPTY but mirror has $mirror_count files."
        echo "  Refusing to mark files absent (possible failed restore or storage bug)."
        echo "  To override: manually delete files from the source and re-run."
        return 0
    fi

    # Detect absent files and create dated markers on first observation
    local new_absence_count=0
    for mirror_file in "$mirror_dir"/*; do
        [ -f "$mirror_file" ] || continue
        local basename
        basename="$(basename "$mirror_file")"
        if [ ! -f "$src_dir/$basename" ]; then
            # File is absent from source — create or update its absence marker
            local marker="$pending_dir/$basename"
            if [ ! -f "$marker" ]; then
                touch "$marker"
                new_absence_count=$((new_absence_count + 1))
            fi
        else
            # File exists in source — remove any stale absence marker
            rm -f "$pending_dir/$basename"
        fi
    done

    # Alert if a large fraction was newly marked absent in this single run
    if [ "$new_absence_count" -gt 0 ] && [ "$mirror_count" -gt 0 ]; then
        local absent_pct=$((new_absence_count * 100 / mirror_count))
        if [ "$absent_pct" -gt 50 ]; then
            echo "$(date -Iseconds) WARNING: $new_absence_count of $mirror_count mirror files ($absent_pct%) newly marked absent in one run."
            echo "  This is unusual — verify the source directory is healthy."
        fi
    fi

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
