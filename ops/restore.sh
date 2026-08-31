#!/bin/bash
# OPS-4: restore a backup bundle produced by ops/backup.sh.
#
# A backup that has never been restored is unverified. This script makes
# the restore path REAL and the rehearsal repeatable:
#   database : psql-applies the newest db-*.sql (backup.sh dumps PLAIN
#              SQL, so psql — not pg_restore), then verifies row counts
#              per table against the dump's COPY blocks.
#   CV files : re-uploads the bundle's Storage export into the bucket
#              (x-upsert POSTs — the storage.py save() pattern) and
#              verifies by re-listing.
#
# SAFETY: refuses to touch a database that already has tables unless
# --force (which DROP SCHEMA public CASCADE first — destructive). The
# runbook's rehearsal procedure (docs/deploy/WO-07-runbook.md → Restore)
# says it anyway: REHEARSE AGAINST A THROWAWAY DATABASE, never prod.
#
# Usage:
#   ops/restore.sh [--force] [--dry-run] [--db-only|--storage-only] <bundle-dir>
#
# Environment (same resolution as backup.sh: env first, then backend/.env):
#   DATABASE_URL               target database (REQUIRED for the DB part)
#   SUPABASE_URL / SUPABASE_SERVICE_KEY / SUPABASE_STORAGE_BUCKET
#                              target bucket (REQUIRED for the storage part)

set -euo pipefail

die() { echo "restore: ERROR: $*" >&2; exit 1; }
usage() {
    # print the leading comment block (skip the shebang), stop at the
    # first non-comment line — a line range would bleed into the code
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    exit 2
}

FORCE=0 DRY_RUN=0 DB_ONLY=0 STORAGE_ONLY=0 BUNDLE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force)        FORCE=1 ;;
        --dry-run)      DRY_RUN=1 ;;
        --db-only)      DB_ONLY=1 ;;
        --storage-only) STORAGE_ONLY=1 ;;
        -h|--help)      usage ;;
        -*)             echo "unknown option: $1" >&2; usage ;;
        *)
            if [ -n "$BUNDLE" ]; then usage; fi
            BUNDLE="$1" ;;
    esac
    shift
done
[ -n "$BUNDLE" ] || usage
if [ "$DB_ONLY" -eq 1 ] && [ "$STORAGE_ONLY" -eq 1 ]; then
    die "--db-only and --storage-only are mutually exclusive"
fi
[ -d "$BUNDLE" ] || die "bundle directory not found: $BUNDLE"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$OPS_DIR/.." && pwd)"
# shellcheck source=storage_backup_lib.sh
. "$OPS_DIR/storage_backup_lib.sh"
# Overridable for tests (ops/test_storage_backup_lib.sh dry-runs this
# script against a throwaway env file); defaults to the real backend/.env.
SB_ENV_FILE="${SB_ENV_FILE:-$ROOT/backend/.env}"

# masked_url URL — print host/db only; NEVER log the password.
masked_url() { printf '%s' "$1" | sed -E 's#(://[^:/@]+:)[^@]+@#\1***@#'; }

# --- database part ------------------------------------------------------

dump_table_counts() {
    # stdout: "<table> <rows>" per public-schema COPY block. pg_dump emits
    # a COPY ... FROM stdin; block for every dumped table (empty tables
    # get an empty block), so this covers the whole public schema.
    awk '
        /^COPY public\./ {
            t = $0
            sub(/^COPY public\./, "", t)
            sub(/ .*/, "", t)
            table = t; rows = 0; incopy = 1; next
        }
        incopy && /^\\\.$/ { printf "%s %d\n", table, rows; incopy = 0; next }
        incopy { rows++ }
    ' "$1"
}

find_dump() {
    local dumps
    dumps=$(cd "$BUNDLE" && ls db-*.sql 2>/dev/null | sort) || true
    [ -n "$dumps" ] || return 1
    printf '%s/%s\n' "$BUNDLE" "$(printf '%s\n' "$dumps" | tail -1)"
}

DUMP=""
DATABASE_URL="${DATABASE_URL:-$(sb_env_or_file DATABASE_URL)}"
# libpq wants postgresql://; our .env carries the SQLAlchemy dialect prefix.
PGURL="${DATABASE_URL/postgresql+psycopg/postgresql}"

restore_database() {
    local existing
    existing=$("$PSQL" -d "$PGURL" -tAc \
        "SELECT count(*) FROM pg_tables WHERE schemaname='public'") \
        || die "cannot query target database: $(masked_url "$PGURL")"
    if [ "$existing" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
        die "target $(masked_url "$PGURL") already has $existing public table(s) — \
restoring would collide. Rehearse against a THROWAWAY database, or pass \
--force to DROP SCHEMA public CASCADE first (DESTROYS all existing data)."
    fi
    if [ "$existing" -gt 0 ]; then
        echo "WARNING: --force — dropping public schema on $(masked_url "$PGURL") (destructive)" >&2
        "$PSQL" -d "$PGURL" -tAc "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" \
            || die "DROP/CREATE SCHEMA public failed"
    fi
    echo "restoring $DUMP ($(du -h "$DUMP" | cut -f1)) into $(masked_url "$PGURL")"
    "$PSQL" -d "$PGURL" -v ON_ERROR_STOP=1 -f "$DUMP" \
        || die "psql restore failed (ON_ERROR_STOP on — the target is now PARTIAL; re-run against a clean database)"

    # Verify: every table's live row count must equal its COPY-row count
    # in the dump. A restore that "ran" but silently left tables empty is
    # exactly the failure a rehearsal exists to catch.
    local mismatch=0 checked=0 table expected actual
    while read -r table expected; do
        actual=$("$PSQL" -d "$PGURL" -tAc "SELECT count(*) FROM public.\"$table\"") \
            || die "cannot count public.$table after restore"
        checked=$((checked + 1))
        if [ "$actual" != "$expected" ]; then
            echo "COUNT MISMATCH public.$table: dump=$expected restored=$actual" >&2
            mismatch=$((mismatch + 1))
        fi
    done < <(dump_table_counts "$DUMP")
    echo "database restore verified: $checked tables, $mismatch mismatches"
    [ "$mismatch" -eq 0 ] || die "row-count verification failed — do NOT trust this restore"
}

# --- storage part -------------------------------------------------------

STORAGE_DIR=""
resolve_storage_dir() {
    local d="$BUNDLE/storage/$SB_BUCKET" n
    if [ -d "$d" ]; then STORAGE_DIR="$d"; return 0; fi
    if [ -d "$BUNDLE/storage" ]; then
        n=$(find "$BUNDLE/storage" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
        if [ "$n" -eq 1 ]; then
            STORAGE_DIR=$(find "$BUNDLE/storage" -mindepth 1 -maxdepth 1 -type d)
            echo "NOTE: bundle's storage dir is '$(basename "$STORAGE_DIR")' (backed-up bucket name differs from target '$SB_BUCKET')" >&2
            return 0
        fi
    fi
    return 1
}

# --- plan / dry-run -----------------------------------------------------

if [ "$STORAGE_ONLY" -ne 1 ]; then
    PSQL=$(command -v psql || echo /opt/homebrew/opt/libpq/bin/psql)
    [ -x "$PSQL" ] || die "psql not found (brew install libpq / postgresql)"
    DUMP=$(find_dump) || die "no db-*.sql dump in $BUNDLE \
(sqlite db-*.db bundles are pre-Supabase dev backups — use ops/migrate_sqlite_to_supabase.py for those)"
    [ -n "$DATABASE_URL" ] || die "DATABASE_URL not set and not found in $SB_ENV_FILE"
fi

if [ "$DB_ONLY" -ne 1 ]; then
    if ! sb_resolve_config; then
        die "SUPABASE_URL / SUPABASE_SERVICE_KEY not set and not found in $SB_ENV_FILE"
    fi
    resolve_storage_dir || die "no storage export in this bundle (pre-P0-5 bundle? re-run ops/backup.sh with Storage creds to produce one)"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry run — nothing will be modified, no network calls:"
    if [ "$STORAGE_ONLY" -ne 1 ]; then
        echo "  database: $DUMP ($(du -h "$DUMP" | cut -f1)) → $(masked_url "$PGURL")"
        echo "    tables in dump: $(dump_table_counts "$DUMP" | wc -l | tr -d ' ')  (live row counts verified post-restore)"
    fi
    if [ "$DB_ONLY" -ne 1 ]; then
        echo "  storage:  $(find "$STORAGE_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ') files from $STORAGE_DIR → bucket '$SB_BUCKET' at $SB_URL (x-upsert, verified by re-listing)"
    fi
    echo "  safety:   a non-empty target database is refused without --force"
    exit 0
fi

if [ "$STORAGE_ONLY" -ne 1 ]; then
    restore_database
fi
if [ "$DB_ONLY" -ne 1 ]; then
    echo "restoring CVs from $STORAGE_DIR into bucket '$SB_BUCKET' at $SB_URL (x-upsert: existing objects with the same name are OVERWRITTEN)"
    storage_restore "$STORAGE_DIR" || die "storage restore failed"
fi
echo "restore complete."
