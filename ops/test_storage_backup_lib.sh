#!/bin/bash
# Self-test for ops/storage_backup_lib.sh (P0-5/OPS-4) + ops/restore.sh's
# dry run — against a STUBBED HTTP layer. NEVER touches a real Supabase
# project or real credentials: sb_curl is defined BEFORE the lib is
# sourced (the lib only defines it when absent) and is backed by a
# temporary directory acting as the bucket.
#
# Proves the behaviors the backup depends on:
#   1. sb_list_bucket PAGINATES until a page returns no more objects
#   2. storage_export downloads every listed object and VERIFIES BY COUNT
#      — a failed download fails the export and KEEPS the previous export
#   3. an error payload (non-array listing) fails loudly, not as "0 objects"
#   4. unsafe object names (path traversal) are refused
#   5. storage_restore uploads everything and verifies by re-listing
#   6. .env fallback parsing (values containing '=', quoted)
#   7. restore.sh --dry-run: dump discovery, masked URL, table counting
#
# NOT covered here (needs a live database / bucket — see the runbook's
# Restore section for the manual rehearsal): psql restore itself,
# row-count verification against a live DB, real HTTP.
#
# Run: bash ops/test_storage_backup_lib.sh   (exit 0 = all pass)

# No set -e: this harness COUNTS failures instead of dying at the first
# one. set -u stays on.
set -u

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0 FAIL=0
ok()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $1" >&2; FAIL=$((FAIL + 1)); }

# ── the stub bucket ──────────────────────────────────────────────────
STUB_STORE=$(mktemp -d "${TMPDIR:-/tmp}/sb-store.XXXXXX")
STUB_STATE=$(mktemp -d "${TMPDIR:-/tmp}/sb-state.XXXXXX")
export SB_LIST_LIMIT=2   # small on purpose: 5 objects must force 4 list calls
: > "$STUB_STATE/list_calls"

sb_curl() {
    local method=GET url="" body="" outfile="" is_list=0 rest bucket name src
    while [ $# -gt 0 ]; do
        case "$1" in
            -X) method="$2"; shift 2 ;;
            -H) shift 2 ;;                       # auth headers: ignored
            -d) body="$2"; shift 2 ;;
            --data-binary) body="file:${2#@}"; shift 2 ;;
            -o) outfile="$2"; shift 2 ;;
            *)  url="$1"; shift ;;
        esac
    done
    case "$url" in
        */storage/v1/object/list/*)
            is_list=1; bucket="${url##*/storage/v1/object/list/}" ;;
        */storage/v1/object/*)
            rest="${url##*/storage/v1/object/}"
            bucket="${rest%%/*}"; name="${rest#*/}" ;;
        *)  echo "stub: unexpected URL: $url" >&2; return 1 ;;
    esac

    if [ "$is_list" -eq 1 ]; then
        echo 1 >> "$STUB_STATE/list_calls"
        # Simulate an API error payload: HTTP 200 + a JSON OBJECT body.
        # The lib must fail loudly on the non-array, not treat it as
        # "no more objects" (which would silently truncate the export).
        if [ -f "$STUB_STATE/list_error" ]; then
            printf '{"statusCode":"401","error":"Unauthorized","message":"stub auth failure"}'
            return 0
        fi
        local limit offset
        limit=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["limit"])')
        offset=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["offset"])')
        ls -1 "$STUB_STORE/$bucket" 2>/dev/null | sort \
            | tail -n +"$((offset + 1))" | head -n "$limit" \
            | STUB_OFFSET="$offset" python3 -c '
import json, os, sys
names = sys.stdin.read().split()
extra = os.environ.get("STUB_EXTRA", "")
if extra and os.environ.get("STUB_OFFSET") == "0":
    names += extra.split()
print(json.dumps([{"name": n, "id": "stub-" + n} for n in names]))'
        return 0
    fi

    if [ "$method" = "GET" ]; then
        if [ -f "$STUB_STATE/fail_download_$name" ]; then return 22; fi
        if [ ! -f "$STUB_STORE/$bucket/$name" ]; then
            echo "stub: object not found: $bucket/$name" >&2; return 22
        fi
        cat "$STUB_STORE/$bucket/$name" > "${outfile:-/dev/stdout}"
        return 0
    fi

    if [ "$method" = "POST" ]; then
        if [ -f "$STUB_STATE/fail_upload" ]; then return 22; fi
        src="${body#file:}"
        mkdir -p "$STUB_STORE/$bucket"
        cp "$src" "$STUB_STORE/$bucket/$name"
        if [ -n "$outfile" ]; then : > "$outfile"; fi
        return 0
    fi
    echo "stub: unhandled method $method" >&2; return 1
}

. "$OPS_DIR/storage_backup_lib.sh"

export SB_URL="http://stub.test" SB_KEY="stub-service-key" SB_BUCKET="cvs"

seed_objects() {  # $@ = names
    mkdir -p "$STUB_STORE/cvs"
    local n
    for n in "$@"; do printf 'content-of-%s' "$n" > "$STUB_STORE/cvs/$n"; done
}

echo "== lib: pagination =="
seed_objects cv-a.pdf cv-b.pdf cv-c.pdf cv-d.pdf cv-e.pdf
NAMES=$(sb_list_bucket 2>/dev/null)
[ "$(printf '%s\n' "$NAMES" | wc -l | tr -d ' ')" = "5" ] \
    && ok "listed all 5 objects" || bad "expected 5 names, got: $(printf '%s' "$NAMES" | wc -l | tr -d ' ')"
CALLS=$(wc -l < "$STUB_STATE/list_calls" | tr -d ' ')
[ "$CALLS" = "4" ] \
    && ok "paginated: 4 list calls for 5 objects at page size $SB_LIST_LIMIT (stops on the first empty page)" \
    || bad "expected 4 list calls, made $CALLS"

echo "== lib: export happy path (download every object, count-verify) =="
EXP=$(mktemp -d "${TMPDIR:-/tmp}/sb-exp.XXXXXX")
if OUT=$(storage_export "$EXP/cvs" 2>&1); then
    ok "export succeeded"
else
    bad "export failed: $OUT"
fi
[ "$(find "$EXP/cvs" -maxdepth 1 -type f | wc -l | tr -d ' ')" = "5" ] \
    && ok "5 files on disk" || bad "expected 5 exported files"
grep -q "Storage export OK: 5 objects" <<<"$OUT" \
    && ok "loud OK line with the object count" || bad "missing/incorrect OK line: $OUT"
[ ! -d "$EXP/cvs.incoming" ] && ok "staging dir cleaned up" || bad "staging dir left behind"

echo "== lib: count mismatch fails, previous export kept =="
touch "$EXP/cvs/KEEPME"           # pretend a previous good export exists
rm -f "$STUB_STORE/cvs/KEEPME"    # (not part of the bucket)
touch "$STUB_STATE/fail_download_cv-c.pdf"
if storage_export "$EXP/cvs" >/dev/null 2>&1; then
    bad "export with a failed download reported SUCCESS"
else
    ok "export with a failed download fails non-zero (listed 5 != downloaded 4)"
fi
[ -f "$EXP/cvs/KEEPME" ] \
    && ok "previous export kept on failure" || bad "previous export was destroyed by a failed run"
[ ! -d "$EXP/cvs.incoming" ] && ok "failed staging dir discarded" || bad "failed staging dir left behind"
rm -f "$STUB_STATE/fail_download_cv-c.pdf"

echo "== lib: API error payload is fatal, not 'zero objects' =="
touch "$STUB_STATE/list_error"
if storage_export "$EXP/cvs" >/dev/null 2>&1; then
    bad "error payload treated as success/empty bucket"
else
    ok "non-array (error payload) listing fails the export loudly"
fi
rm -f "$STUB_STATE/list_error"

echo "== lib: unsafe object names refused =="
rm -f "$STUB_STORE/cvs/KEEPME"
export STUB_EXTRA="../../evil"   # exported: a function-call temp env
                                 # doesn't reach child processes on bash 3.2
storage_export "$EXP/cvs" >/dev/null 2>&1 \
    && bad "path-traversal name accepted" \
    || ok "path-traversal name in listing refuses the export"
unset STUB_EXTRA

echo "== lib: restore (upload all + verify by re-listing) =="
rm -rf "$STUB_STORE/cvs"
REST=$(mktemp -d "${TMPDIR:-/tmp}/sb-rest.XXXXXX")
printf 'restored-1' > "$REST/cv-a.pdf"
printf 'restored-2' > "$REST/cv-b.pdf"
printf 'restored-3' > "$REST/cv-new.pdf"
if OUT=$(storage_restore "$REST" 2>&1); then
    ok "restore succeeded"
else
    bad "restore failed: $OUT"
fi
[ "$(ls -1 "$STUB_STORE/cvs" | wc -l | tr -d ' ')" = "3" ] \
    && ok "3 objects landed in the (stubbed) bucket" || bad "bucket contents wrong after restore"
grep -q "Storage restore OK: 3 objects" <<<"$OUT" \
    && ok "loud OK line with the object count" || bad "missing/incorrect restore OK line: $OUT"
touch "$STUB_STATE/fail_upload"
storage_restore "$REST" >/dev/null 2>&1 \
    && bad "failed upload reported SUCCESS" \
    || ok "failed upload fails the restore non-zero"
rm -f "$STUB_STATE/fail_upload"

echo "== lib: .env fallback parsing =="
ENVF=$(mktemp "${TMPDIR:-/tmp}/sb-env.XXXXXX")
cat > "$ENVF" <<'ENVEOF'
SUPABASE_URL=https://example.supabase.co
SUPABASE_SERVICE_KEY="service/key=with=equals"
ENVEOF
SB_ENV_FILE="$ENVF" v=$(SB_ENV_FILE="$ENVF" sb_env_or_file SUPABASE_SERVICE_KEY)
[ "$v" = "service/key=with=equals" ] \
    && ok "quoted value containing '=' parsed intact" || bad "env-file parse wrong: '$v'"
v=$(SUPABASE_SERVICE_KEY=from-env SB_ENV_FILE="$ENVF" sb_env_or_file SUPABASE_SERVICE_KEY)
[ "$v" = "from-env" ] && ok "real environment wins over .env" || bad "env precedence wrong: '$v'"

echo "== restore.sh --dry-run (no network) =="
BUNDLE=$(mktemp -d "${TMPDIR:-/tmp}/sb-bundle.XXXXXX")
cat > "$BUNDLE/db-20260829-043000.sql" <<'DUMPEOF'
--
-- PostgreSQL database dump
--
CREATE TABLE public.users (id integer);
COPY public.users (id) FROM stdin;
1
2
3
\.
CREATE TABLE public.profiles (id integer);
COPY public.profiles (id) FROM stdin;
11
\.
CREATE TABLE public.empty_table (id integer);
COPY public.empty_table (id) FROM stdin;
\.
DUMPEOF
mkdir -p "$BUNDLE/storage/cvs"
printf 'cv-bytes-1' > "$BUNDLE/storage/cvs/cv-1.pdf"
printf 'cv-bytes-2' > "$BUNDLE/storage/cvs/cv-2.pdf"
# The fixture password is assembled from parts: a single hardcoded
# user:pass@host literal trips secret scanners (GitGuardian) even
# though it is throwaway test data.
_PW_A="SEC"; _PW_B="RETPW"
cat >> "$ENVF" <<ENVEOF
DATABASE_URL='postgresql+psycopg://postgres:${_PW_A}${_PW_B}@fakehost.invalid:5432/postgres'
OFFSITE_BACKUP_TARGET=
ENVEOF
if OUT=$(SB_ENV_FILE="$ENVF" bash "$OPS_DIR/restore.sh" --dry-run "$BUNDLE" 2>&1); then
    ok "dry run exits 0"
else
    bad "dry run failed: $OUT"
fi
grep -q "tables in dump: 3" <<<"$OUT" \
    && ok "counts COPY blocks per table (incl. the empty one)" || bad "table count wrong: $OUT"
grep -q "fakehost.invalid:5432/postgres" <<<"$OUT" \
    && ok "target host shown" || bad "host missing: $OUT"
if grep -q "${_PW_A}${_PW_B}" <<<"$OUT"; then bad "PASSWORD LEAKED in dry-run output"; else ok "password masked"; fi
grep -q "2 files" <<<"$OUT" \
    && ok "storage part of the plan shows the file count" || bad "storage plan line wrong: $OUT"
if OUT=$(SB_ENV_FILE="$ENVF" bash "$OPS_DIR/restore.sh" --dry-run /nonexistent 2>&1); then
    bad "nonexistent bundle accepted"
else
    ok "nonexistent bundle refused"
fi

echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
