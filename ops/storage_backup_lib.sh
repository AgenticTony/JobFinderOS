#!/bin/bash
# Shared Supabase Storage export/restore helpers (P0-5, OPS-4).
#
# Production CVs live in the PRIVATE Supabase Storage bucket `cvs`
# (render.yaml → backend/app/services/storage.py). pg_dump captures rows,
# NOT Storage objects — before this lib existed, no step anywhere exported
# the bucket, so bucket loss meant permanent loss of every user's original
# CV (the original CV is immutable — irreplaceable). backup.sh sources
# this to export; restore.sh sources it to re-upload.
#
# HTTP surface — the SAME official-REST + service-key pattern as
# ops/provision_supabase_storage.py and backend/app/services/storage.py:
#   list:     POST {SB_URL}/storage/v1/object/list/{bucket}
#             body {"prefix","limit","offset"} — paginated until an empty page
#   download: GET  {SB_URL}/storage/v1/object/{bucket}/{name}   (service key)
#   upload:   POST {SB_URL}/storage/v1/object/{bucket}/{name}   (x-upsert)
#
# Configuration (all optional — callers resolve and export):
#   SB_URL, SB_KEY, SB_BUCKET   the project URL, service key, bucket name
#   SB_ENV_FILE                 .env path used by sb_env_or_file fallback
#   SB_PREFIX                   list prefix (default "")
#   SB_LIST_LIMIT               page size (default 100)
#   SB_PYTHON                   JSON parser binary (default python3)
#
# Testing: ALL HTTP goes through sb_curl(). Define sb_curl BEFORE sourcing
# this file to stub the transport (ops/test_storage_backup_lib.sh does
# exactly that; it never touches a real Supabase project).

# Single HTTP choke-point. Default: curl, silent but loud on errors
# (-f: HTTP >= 400 exits non-zero). Tests pre-define sb_curl to override.
if ! declare -F sb_curl >/dev/null 2>&1; then
    sb_curl() {
        curl --silent --show-error --fail --max-time 300 "$@"
    }
fi

# sb_env_or_file VAR — value from the environment, else the VAR= line of
# $SB_ENV_FILE. Same parse as backup.sh's DATABASE_URL resolution:
# keep everything after the FIRST '=' (passwords may contain '=') and
# strip one level of surrounding quotes. `|| true`: a missing file or
# missing line must not kill the caller under set -e.
sb_env_or_file() {
    local var="$1" v
    # NB: separate statement — in one `local var=.. v="${!var}"` line, the
    # indirection runs before `var` is assigned (observed on bash 3.2).
    v="${!var:-}"
    if [ -z "$v" ] && [ -n "${SB_ENV_FILE:-}" ] && [ -f "$SB_ENV_FILE" ]; then
        v=$(grep "^$var=" "$SB_ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- \
            | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true)
    fi
    printf '%s' "$v"
}

# sb_resolve_config — resolve SB_URL/SB_KEY/SB_BUCKET from env or .env.
# Returns 0 when URL+key are available, 1 otherwise (caller decides how
# loud to be — backup.sh warns, restore.sh refuses).
sb_resolve_config() {
    SB_URL="${SB_URL:-$(sb_env_or_file SUPABASE_URL)}"
    SB_KEY="${SB_KEY:-$(sb_env_or_file SUPABASE_SERVICE_KEY)}"
    SB_BUCKET="${SB_BUCKET:-$(sb_env_or_file SUPABASE_STORAGE_BUCKET)}"
    SB_BUCKET="${SB_BUCKET:-cvs}"
    [ -n "$SB_URL" ] && [ -n "$SB_KEY" ]
}

# sb_json_names — stdin: one page of a list response; stdout: the FILE
# object names, one per line. Fails loudly (non-zero + stderr) on an
# unparseable body or a non-array body — Supabase errors arrive as JSON
# objects ({"statusCode":..,"message":..}), and treating one as "zero
# objects on this page" would end pagination early and silently truncate
# the export.
sb_json_names() {
    # -c (NOT a stdin heredoc): the data arrives ON stdin, so the program
    # must not occupy it.
    "${SB_PYTHON:-python3}" -c '
import json, sys

try:
    data = json.load(sys.stdin)
except Exception as exc:  # noqa: BLE001 - any parse failure is fatal here
    sys.exit("STORAGE-LIST-ERROR: unparseable list response: %s" % exc)

if not isinstance(data, list):
    sys.exit("STORAGE-LIST-ERROR: list response is not an array "
             "(error payload?): %.200s" % (data,))

for entry in data:
    if not isinstance(entry, dict):
        sys.exit("STORAGE-LIST-ERROR: non-object entry in list: %.120s" % (entry,))
    name = entry.get("name")
    # Folder entries carry id:null — only real objects have an id.
    if name and entry.get("id") is not None:
        print(name)
'
}

# sb_list_bucket — stdout: EVERY object name in the bucket, one per line.
# Paginates (limit/offset) until a page returns no more objects.
sb_list_bucket() {
    local offset=0 limit="${SB_LIST_LIMIT:-100}" page names
    while :; do
        page=$(sb_curl -X POST \
            "$SB_URL/storage/v1/object/list/$SB_BUCKET" \
            -H "apikey: $SB_KEY" \
            -H "Authorization: Bearer $SB_KEY" \
            -H "Content-Type: application/json" \
            -d "{\"prefix\":\"${SB_PREFIX:-}\",\"limit\":$limit,\"offset\":$offset}") || return 1
        names=$(printf '%s' "$page" | sb_json_names) || return 1
        # An empty page means the listing is complete.
        [ -z "$names" ] && return 0
        printf '%s\n' "$names"
        offset=$((offset + limit))
    done
}

# sb_name_is_safe — the app only ever writes names matching
# storage.py safe_name() ([A-Za-z0-9._-]+). Anything else in a listing is
# either foreign or hostile (e.g. "../x" path traversal) — refuse it
# rather than use it as a filesystem path or URL segment.
sb_name_is_safe() {
    case "$1" in
        *[!A-Za-z0-9._-]* | "" | "." | "..") return 1 ;;
        *) return 0 ;;
    esac
}

# sb_download NAME DEST — authenticated object GET (storage.py read()
# pattern). Non-zero on HTTP error (sb_curl -f).
sb_download() {
    sb_curl -X GET "$SB_URL/storage/v1/object/$SB_BUCKET/$1" \
        -H "apikey: $SB_KEY" \
        -H "Authorization: Bearer $SB_KEY" \
        -o "$2"
}

# sb_upload FILE NAME — POST raw bytes with x-upsert (storage.py save()
# pattern). Non-zero on HTTP error.
sb_upload() {
    sb_curl -X POST "$SB_URL/storage/v1/object/$SB_BUCKET/$2" \
        -H "apikey: $SB_KEY" \
        -H "Authorization: Bearer $SB_KEY" \
        -H "x-upsert: true" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@$1" \
        -o /dev/null
}

# storage_export DEST_DIR — export the whole bucket into DEST_DIR,
# replacing any previous export ONLY after the new one verified.
#   1. list (paginated)  2. download each object
#   3. VERIFY BY COUNT (listed == downloaded; any failure is fatal)
#   4. atomic-ish swap: stage dir → dest
# A count mismatch or any download failure keeps the PREVIOUS export and
# returns non-zero — a backup that claims CVs it doesn't have is worse
# than a failed run, because nobody re-checks a "successful" one.
storage_export() {
    local dest="$1" stage="${1}.incoming" listing
    listing=$(mktemp "${TMPDIR:-/tmp}/sb-list.XXXXXX")
    rm -rf "$stage"
    mkdir -p "$stage"

    if ! sb_list_bucket > "$listing"; then
        echo "$(date -Iseconds) STORAGE EXPORT FAILED: cannot list bucket '$SB_BUCKET' at $SB_URL" >&2
        rm -rf "$stage" "$listing"
        return 1
    fi

    local listed name failed=0 downloaded=0
    listed=$(wc -l < "$listing" | tr -d ' ')

    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if ! sb_name_is_safe "$name"; then
            echo "$(date -Iseconds) STORAGE EXPORT FAILED: unsafe object name in listing: '$name' — investigate the bucket" >&2
            failed=$((failed + 1))
            continue
        fi
        if sb_download "$name" "$stage/$name"; then
            downloaded=$((downloaded + 1))
        else
            echo "$(date -Iseconds) STORAGE EXPORT: download failed: $name" >&2
            failed=$((failed + 1))
        fi
    done < "$listing"

    local ondisk
    ondisk=$(find "$stage" -maxdepth 1 -type f | wc -l | tr -d ' ')
    if [ "$listed" != "$ondisk" ] || [ "$failed" -ne 0 ]; then
        echo "$(date -Iseconds) STORAGE EXPORT FAILED: listed=$listed on-disk=$ondisk failures=$failed — previous export kept, staged files discarded" >&2
        rm -rf "$stage" "$listing"
        return 1
    fi

    rm -rf "$dest"
    mv "$stage" "$dest"
    rm -f "$listing"
    echo "$(date -Iseconds) Storage export OK: $listed objects in $dest"
    if [ "$listed" -eq 0 ]; then
        echo "$(date -Iseconds) NOTE: bucket '$SB_BUCKET' listed ZERO objects (empty or unused — expected only before the first production upload)" >&2
    fi
    return 0
}

# storage_restore SRC_DIR — upload every file from SRC_DIR into the
# bucket, then VERIFY by re-listing: each uploaded object must appear.
# Uploads use x-upsert, so re-running is idempotent.
storage_restore() {
    local src="$1" f name total=0 uploaded=0 after missing=0
    if [ ! -d "$src" ]; then
        echo "storage_restore: no such directory: $src" >&2
        return 1
    fi

    for f in "$src"/*; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        if ! sb_name_is_safe "$name"; then
            echo "storage_restore: REFUSING unsafe file name: '$name'" >&2
            return 1
        fi
        total=$((total + 1))
        if sb_upload "$f" "$name"; then
            uploaded=$((uploaded + 1))
        else
            echo "storage_restore: upload FAILED: $name" >&2
        fi
    done

    if [ "$uploaded" -ne "$total" ]; then
        echo "STORAGE RESTORE FAILED: uploaded $uploaded of $total files" >&2
        return 1
    fi

    # Count verification against the live bucket: every restored object
    # must now be listed (catches silent upload drops / auth oddities).
    after=$(sb_list_bucket) || { echo "STORAGE RESTORE VERIFY FAILED: cannot re-list bucket '$SB_BUCKET'" >&2; return 1; }
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if ! printf '%s\n' "$after" | grep -Fxq "$name"; then
            echo "STORAGE RESTORE VERIFY FAILED: '$name' not present in bucket after upload" >&2
            missing=$((missing + 1))
        fi
    done < <(find "$src" -maxdepth 1 -type f -exec basename {} \;)
    if [ "$missing" -ne 0 ]; then
        return 1
    fi
    echo "Storage restore OK: $uploaded objects in bucket '$SB_BUCKET' (verified by re-listing)"
    return 0
}
