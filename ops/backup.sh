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
# APPEND-ONLY: no --delete. Original CVs are non-regenerable (invariant #1),
# so partial loss must NEVER propagate to the mirror. The mirror grows
# additively; a periodic manual review can clean orphans if needed.
# (macOS openrsync's --backup-dir is unreliable; append-only is simpler
# and strictly safer than any --delete variant.)
if [ -d "$PROJECT/backend/uploads/cvs" ]; then
    rsync -a "$PROJECT/backend/uploads/cvs/" "$BACKUP_DIR/cvs/"
    echo "$(date -Iseconds) CVs mirrored: $(ls "$BACKUP_DIR/cvs/" | wc -l | tr -d ' ') files (append-only)"
fi

# --- Draft PDFs (tailored CVs + cover letters) — same append-only policy
if [ -d "$PROJECT/backend/uploads/drafts" ]; then
    mkdir -p "$BACKUP_DIR/drafts"
    rsync -a "$PROJECT/backend/uploads/drafts/" "$BACKUP_DIR/drafts/"
fi

# --- Rotation: remove backups older than KEEP_DAYS ---
find "$BACKUP_DIR" -name "db-*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
DB_COUNT=$(ls "$BACKUP_DIR"/db-*.db 2>/dev/null | wc -l | tr -d ' ')
echo "$(date -Iseconds) rotation complete: $DB_COUNT db backups retained (keeping $KEEP_DAYS days)"
