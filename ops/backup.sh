#!/bin/bash
# Scheduled backup of the database (Supabase pg_dump; sqlite3 .backup for
# dev SQLite) + the PRODUCTION CV bucket (Supabase Storage export —
# pg_dump captures rows, NOT Storage objects) + backend/uploads/ (the
# local-dev CV/draft dirs, which receive nothing in production).
# Runs via launchd (com.jobfinderos.backup) at 04:30 daily.
# Timestamped copies, rotated (30 days kept), stored off the working directory.

set -euo pipefail

PROJECT="/Users/anthonyforan/Desktop/JobFinderOS"
BACKUP_DIR="$HOME/backups/jobfinderos"
KEEP_DAYS=30

# Supabase Storage export/restore helpers (P0-5). All HTTP goes through
# the lib's sb_curl(); creds resolve from env or backend/.env below.
OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=storage_backup_lib.sh
. "$OPS_DIR/storage_backup_lib.sh"
SB_ENV_FILE="$PROJECT/backend/.env"

mkdir -p "$BACKUP_DIR/cvs" "$BACKUP_DIR/drafts"

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

# --- Database backup: pg_dump (Postgres/Supabase) or sqlite3 .backup ---
# Resolve DATABASE_URL from .env BEFORE the branch test (review r2: the
# fallback sat INSIDE the if-body it was meant to enable, so the nightly
# launchd run — which exports no DATABASE_URL — silently took the SQLite
# branch and backed up the FROZEN pre-migration file while the live data
# had moved to Supabase. A backup that runs, verifies, and contains the
# wrong database is discovered at restore time.)
DATABASE_URL="${DATABASE_URL:-}"
if [ -z "$DATABASE_URL" ]; then
    # f2- keeps everything after the FIRST '=' (sslmode params, passwords
    # containing '='); sed strips the surrounding quotes a .env may carry.
    # Review r3: `cut -d= -f2` truncated at the inner '=' and kept the
    # opening quote — the quoted form fails the ^postgre grep and silently
    # reverts to the SQLite branch (the exact r2 bug, restored by formatting).
    # `|| true` (review r4): this is a bare assignment in an if-body — a
    # missing .env or missing line made the substitution's exit status
    # kill the whole script under set -e, BEFORE rotation and the
    # off-site sync (the sibling of the pg_dump hazard three blocks down).
    DATABASE_URL=$(grep '^DATABASE_URL=' "$PROJECT/backend/.env" 2>/dev/null | head -1 | cut -d= -f2- \
        | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true)
    if [ -z "$DATABASE_URL" ]; then
        echo "$(date -Iseconds) WARNING: DATABASE_URL not in env or $PROJECT/backend/.env — falling back to the SQLite branch (is the checkout relocated?)" >&2
    fi
fi

# A database failure must NOT gate the off-site sync (review r3): the
# off-site copy of the CV mirror is independent of the dump, and the
# script's own comment below calls it "the only unrecoverable-risk
# item". Record the failure, still sync off-site, exit non-zero at the
# end — a bad dump costs the dump, not the whole run.
DB_FAILED=0

PG_DUMP=$(command -v pg_dump || echo /opt/homebrew/opt/libpq/bin/pg_dump)
if [ -x "$PG_DUMP" ] && \
   echo "$DATABASE_URL" | grep -q "^postgre"; then
    STAMP=$(date +%Y%m%d-%H%M%S)
    # pg_dump sits in the if-CONDITION: set -e would otherwise kill the
    # script at this line on a crash — before the failure handler, and
    # before the off-site block (the r3 finding was worse than reported).
    # stderr is CAPTURED per-run (r4): the script now survives a failed
    # dump, so the log is the only record — "FAILED" without a reason
    # can't distinguish a transient pooler blip from a rotated password.
    # On success the .err file is removed; on failure it is KEPT.
    # --schema public (OPS-4): the app's entire schema is public (alembic
    # restricts to public; auth/storage schemas are Supabase-managed and
    # unused by the app). An unscoped dump also carries those schemas,
    # and restoring it into ANY fresh Supabase project dies on
    # "relation already exists" under ON_ERROR_STOP — a dump you cannot
    # restore into a throwaway database is an unverified backup.
    if "$PG_DUMP" --schema public --no-owner --no-privileges \
        --dbname "${DATABASE_URL/postgresql+psycopg/postgresql}" \
        --file "$BACKUP_DIR/db-$STAMP.sql" 2> "$BACKUP_DIR/db-$STAMP.dump.err" && \
       [ -s "$BACKUP_DIR/db-$STAMP.sql" ]; then
        rm -f "$BACKUP_DIR/db-$STAMP.dump.err"
        echo "$(date -Iseconds) pg_dump OK: db-$STAMP.sql ($(du -h "$BACKUP_DIR/db-$STAMP.sql" | cut -f1))"
    else
        echo "$(date -Iseconds) pg_dump FAILED (non-zero exit or empty file) — continuing to off-site sync; reason in $BACKUP_DIR/db-$STAMP.dump.err:" >&2
        tail -3 "$BACKUP_DIR/db-$STAMP.dump.err" >&2 2>/dev/null || true
        rm -f "$BACKUP_DIR/db-$STAMP.sql"  # never leave a fresh-stamped empty/half dump
        DB_FAILED=1
    fi
    find "$BACKUP_DIR" -maxdepth 1 -name "db-*.sql" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
else
    # SQLite (dev / pre-migration): the .backup choreography
    NOW=$(date +%Y%m%d-%H%M%S)
    if [ ! -f "$PROJECT/backend/jobfinderos.db" ]; then
        echo "$(date -Iseconds) WARNING: no database at $PROJECT/backend/jobfinderos.db (and DATABASE_URL unresolved — see warning above)" >&2
        DB_FAILED=1
    elif sqlite3 "$PROJECT/backend/jobfinderos.db" ".backup '$BACKUP_DIR/db-$NOW.db'"; then
        echo "$(date -Iseconds) sqlite backup OK: db-$NOW.db"
    else
        echo "$(date -Iseconds) sqlite backup FAILED — continuing to off-site sync" >&2
        DB_FAILED=1
    fi
fi

# --- Production CV Storage export (P0-5: beta blocker) ---
# pg_dump captures ROWS, not Storage objects: production CVs live in the
# private Supabase bucket (render.yaml sets STORAGE_BACKEND=supabase), so
# the local mirror above receives NOTHING in production. Before this
# step existed, bucket loss = permanent loss of every user's original CV
# (immutable — irreplaceable). The export lands inside the backup bundle
# (so the off-site step below replicates it too); it lists the bucket
# page by page, downloads each object, and FAILS the run on any count
# mismatch between what was listed and what landed on disk.
STORAGE_FAILED=0
if sb_resolve_config; then
    # A Storage failure must NOT gate the off-site sync (same reasoning
    # as the dump's DB_FAILED above): what did succeed still replicates
    # off-site; the failure is recorded and surfaces to launchd at the
    # very end, after everything salvageable was copied.
    if ! storage_export "$BACKUP_DIR/storage/$SB_BUCKET"; then
        echo "$(date -Iseconds) Storage export FAILED — continuing to off-site sync with the previous export" >&2
        STORAGE_FAILED=1
    fi
else
    echo "$(date -Iseconds) WARNING: SUPABASE_URL / SUPABASE_SERVICE_KEY not in env or $SB_ENV_FILE — production CV bucket NOT exported (expected only on STORAGE_BACKEND=local dev machines)" >&2
fi

# --- Rotation: remove database backups older than KEEP_DAYS ---
find "$BACKUP_DIR" -maxdepth 1 -name "db-*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
DB_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 \( -name "db-*.sql" -o -name "db-*.db" \) | wc -l | tr -d ' ')
echo "$(date -Iseconds) rotation complete: $DB_COUNT db backups retained (keeping $KEEP_DAYS days)"

# --- MIG-WO0: OFF-SITE copy (the only unrecoverable-risk item) ---
# The whole script above writes to a directory on the SAME DISK as the
# data it protects. This step replicates the backup set OFF-MACHINE.
#
# OFFSITE_BACKUP_TARGET (env): an rsync-compatible destination —
#   another machine:  user@host:/srv/jobfinderos-backups
#   cloud via rclone: rclone:remote:bucket  (set OFFSITE_CMD=rclone)
#   encrypted:        wrap with gpg --encrypt per-file before sending
#
# VERIFIES the copy landed (file count match) and exits NON-ZERO on
# failure — an off-site copy that silently didn't happen is the same
# as not having one.
OFFSITE_BACKUP_TARGET="${OFFSITE_BACKUP_TARGET:-}"
if [ -n "$OFFSITE_BACKUP_TARGET" ]; then
    SYNC_CMD="${OFFSITE_CMD:-rsync}"
    SRC_COUNT=$(find "$BACKUP_DIR" -type f | wc -l | tr -d ' ')
    if [ "$SYNC_CMD" = "rsync" ]; then
        rsync -a --delete "$BACKUP_DIR/" "$OFFSITE_BACKUP_TARGET/"
    else
        $SYNC_CMD copy "$BACKUP_DIR" "$OFFSITE_BACKUP_TARGET" --transferred 50 2>/dev/null \
            || $SYNC_CMD sync "$BACKUP_DIR" "$OFFSITE_BACKUP_TARGET"
    fi
    if [ $? -ne 0 ]; then
        echo "$(date -Iseconds) OFFSITE FAILED: sync error" >&2
        exit 1
    fi
    # verification: count files at the destination
    if [ "$SYNC_CMD" = "rclone" ]; then
        DST_COUNT=$(rclone lsf -R --files-only "$OFFSITE_BACKUP_TARGET" 2>/dev/null | wc -l | tr -d ' ')
    else
        case "$OFFSITE_BACKUP_TARGET" in
            *:*) DST_COUNT=$(ssh "${OFFSITE_BACKUP_TARGET%%:*}" \
                    "find '${OFFSITE_BACKUP_TARGET#*:}' -type f | wc -l" 2>/dev/null | tr -d ' ') ;;
            *)  DST_COUNT=$(find "$OFFSITE_BACKUP_TARGET" -type f 2>/dev/null | wc -l | tr -d ' ') ;;
        esac
    fi
    if [ "$DST_COUNT" != "$SRC_COUNT" ]; then
        echo "$(date -Iseconds) OFFSITE VERIFY FAILED: src=$SRC_COUNT dst=$DST_COUNT" >&2
        exit 1
    fi
    echo "$(date -Iseconds) off-site OK: $SRC_COUNT files at $OFFSITE_BACKUP_TARGET"
else
    echo "$(date -Iseconds) WARNING: OFFSITE_BACKUP_TARGET not set — backups exist on ONE disk only (MIG-WO0 incomplete)" >&2
fi

# The failures deferred from above (r3 pattern): the off-site sync has now
# run regardless, so surface them to launchd only at the very end. (Plain
# if/fi, not [ ] && — under set -e a false bare [ ] && kills the script.)
FAILED_SUMMARY=""
if [ "$DB_FAILED" -ne 0 ]; then FAILED_SUMMARY="database dump"; fi
if [ "$STORAGE_FAILED" -ne 0 ]; then
    FAILED_SUMMARY="${FAILED_SUMMARY:+$FAILED_SUMMARY + }CV Storage export"
fi
if [ -n "$FAILED_SUMMARY" ]; then
    echo "$(date -Iseconds) BACKUP INCOMPLETE: $FAILED_SUMMARY failed (off-site sync ran; whatever succeeded is safe)" >&2
    exit 1
fi
