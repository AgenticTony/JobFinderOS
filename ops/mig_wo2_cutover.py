#!/usr/bin/env python
"""MIG-WO2 cutover: move the live user rows onto Supabase Auth identities.

What this does (one run, two phases, both gated on --yes):

  PHASE 1 (Supabase): for every local users row, create the Supabase
  auth user (email_confirm=true — they are proven-real beta accounts)
  via the admin API with a generated random password, and write the
  old->new UUID mapping to the snapshot file.

  PHASE 2 (Postgres, ONE transaction): insert each users row under its
  new Supabase UUID (copying columns; hashed_password becomes the
  sentinel — Supabase owns the real secret), repoint the six user_id
  tables (profiles, match_results, application_drafts, applications,
  feedback, ai_usage — the last two carry no FK, they are remapped for
  accounting continuity), delete the old users rows. Commit; then
  re-verify per-table row counts against the pre-write snapshot.

Why insert-new/repoint/delete-old and not UPDATE users.id: the FKs are
plain and NOT DEFERRABLE (account.py's erasure ordering depends on it),
so a PK update would violate constraints mid-statement. This order is
the "proven collision-free" alternative MIGRATION.md asks for: the new
ids are fresh UUIDs, so profiles' UNIQUE(user_id) and match_results'
UNIQUE(user_id, job_id) cannot collide inside the transaction.

Safety:
  - refuses to run without --yes
  - refuses if the users table holds more than --max-users rows (5)
  - writes the snapshot (mapping + FULL original users rows + per-user
    per-table counts) BEFORE touching anything; --verify re-checks
    against it; --reverse restores the old UUIDs and original rows
  - the Postgres phase is a single transaction — any error rolls back
    the whole remap (Supabase users created in phase 1 are then deleted
    again unless --keep-supabase)

Usage (from backend/ so .env loads):
  .venv/bin/python ../ops/mig_wo2_cutover.py --plan     # dry look
  .venv/bin/python ../ops/mig_wo2_cutover.py --yes      # do it
  .venv/bin/python ../ops/mig_wo2_cutover.py --verify   # after
  .venv/bin/python ../ops/mig_wo2_cutover.py --reverse --yes  # undo
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

import httpx

# Resolve backend/ (sibling of ops/) and put it FIRST so app.core.config
# loads backend/.env — the DATABASE_URL there is the LIVE database and
# the SUPABASE_* keys are the real ones.
BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.core.database import DATABASE_URL, engine  # noqa: E402
from app.core.timeutil import utc_now  # noqa: E402
from sqlalchemy import text  # noqa: E402

SNAPSHOT_PATH = Path(__file__).resolve().parent / "mig_wo2_snapshot.json"
USER_TABLES = [
    "profiles",
    "match_results",
    "application_drafts",
    "applications",
    "feedback",
    "ai_usage",
]


def die(msg: str) -> None:
    print(f"ABORT: {msg}", file=sys.stderr)
    sys.exit(1)


def snapshot_counts(conn, user_ids) -> dict:
    """Per-user row counts for every user_id table, keyed by id."""
    out = {}
    for uid in user_ids:
        row = {}
        for table in USER_TABLES:
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :u"),
                {"u": uid},
            ).scalar()
            row[table] = int(n)
        out[str(uid)] = row
    return out


def fetch_local_users(conn) -> list[dict]:
    rows = conn.execute(
        text("SELECT id, email, display_name, is_superuser, created_at "
             "FROM users WHERE is_active = true ORDER BY created_at")
    ).mappings().all()
    return [
        {
            "old_id": str(r["id"]),
            "email": r["email"],
            "display_name": r["display_name"],
            "is_superuser": bool(r["is_superuser"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


def backup_users_rows(conn, old_ids) -> dict:
    """FULL original users rows, for a faithful --reverse. The cutover's
    INSERT drops the fastapi-users password hash (supabase-auth sentinel)
    and flips is_verified — without this backup, reverse() could not
    restore either."""
    out = {}
    for uid in old_ids:
        r = conn.execute(
            text("SELECT id, email, hashed_password, is_active, is_superuser, "
                 "is_verified, display_name, token_version, created_at "
                 "FROM users WHERE id = :o"), {"o": uid}).mappings().one()
        out[str(uid)] = {
            "id": str(r["id"]),
            "email": r["email"],
            "hashed_password": r["hashed_password"],
            "is_active": bool(r["is_active"]),
            "is_superuser": bool(r["is_superuser"]),
            "is_verified": bool(r["is_verified"]),
            "display_name": r["display_name"],
            "token_version": int(r["token_version"] or 0),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
    return out


def supabase_create_user(email: str, password: str) -> str:
    resp = httpx.post(
        f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/users",
        headers={
            "apikey": settings.SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password, "email_confirm": True},
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        die(f"Supabase createUser failed for {email}: {resp.status_code} {resp.text[:200]}")
    return resp.json()["id"]


def supabase_delete_user(user_id: str) -> None:
    resp = httpx.delete(
        f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/users/{user_id}",
        headers={
            "apikey": settings.SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        },
        timeout=15,
    )
    if resp.status_code not in (200, 204, 404):
        print(f"  WARN: supabase delete of {user_id} -> {resp.status_code}", file=sys.stderr)


def cutover(conn, users, args) -> None:
    # ---- pre-write snapshot ----
    old_ids = [u["old_id"] for u in users]
    snap = {
        "created_at": None,
        "mapping": {},           # old_id -> new_id
        "passwords": {},         # email -> generated password (OWNER copies these out)
        "before": snapshot_counts(conn, old_ids),
        "users_backup": backup_users_rows(conn, old_ids),  # for --reverse
    }

    # ---- phase 1: create Supabase identities ----
    created: list[str] = []
    print("\nphase 1: creating Supabase users (email_confirmed)…")
    try:
        for u in users:
            pw = secrets.token_urlsafe(18)
            new_id = supabase_create_user(u["email"], pw)
            snap["mapping"][u["old_id"]] = new_id
            snap["passwords"][u["email"]] = pw
            created.append(new_id)
            print(f"  {u['email']} -> supabase id {new_id}")
    except SystemExit:
        if not args.keep_supabase:
            for nid in created:
                supabase_delete_user(nid)
        raise

    snap["created_at"] = utc_now().isoformat() + "Z"
    SNAPSHOT_PATH.write_text(json.dumps(snap, indent=2))
    print(f"snapshot written: {SNAPSHOT_PATH} (KEEP IT — it is the rollback map)")

    # ---- phase 2: the one-transaction remap ----
    print("\nphase 2: remapping local rows (single transaction)…")
    trans = conn.begin()
    try:
        for old_id, new_id in snap["mapping"].items():
            cols = conn.execute(
                text("SELECT email, is_active, is_superuser, display_name, "
                     "created_at FROM users WHERE id = :o"), {"o": old_id}).mappings().one()
            conn.execute(
                text("INSERT INTO users (id, email, hashed_password, is_active, "
                     "is_superuser, is_verified, display_name, token_version, "
                     "created_at) VALUES (:id, :email, 'supabase-auth', "
                     ":is_active, :is_superuser, true, :display_name, 0, "
                     ":created_at)"),
                {"id": new_id, "email": cols["email"],
                 "is_active": cols["is_active"], "is_superuser": cols["is_superuser"],
                 "display_name": cols["display_name"], "created_at": cols["created_at"]})
            for table in USER_TABLES:
                n = conn.execute(
                    text(f"UPDATE {table} SET user_id = :n WHERE user_id = :o"),
                    {"n": new_id, "o": old_id}).rowcount
                print(f"  {table}: {n} row(s) {old_id[:8]}… -> {new_id[:8]}…")
            conn.execute(text("DELETE FROM users WHERE id = :o"), {"o": old_id})
        trans.commit()
    except Exception:
        trans.rollback()
        print("transaction FAILED — rolled back; cleaning up Supabase users…",
              file=sys.stderr)
        if not args.keep_supabase:
            for nid in created:
                supabase_delete_user(nid)
        raise

    # ---- verify ----
    print("\nverifying against snapshot…")
    after = snapshot_counts(conn, list(snap["mapping"].values()))
    ok = True
    for old_id, new_id in snap["mapping"].items():
        for table in USER_TABLES:
            b = snap["before"][old_id][table]
            a = after[new_id][table]
            if a != b:
                print(f"  MISMATCH {table} {old_id[:8]}…: before={b} after={a}")
                ok = False
    print("row counts verified OK" if ok else "MISMATCHES FOUND — inspect manually")
    print("\npasswords (copy to the users now; they change them via "
          "forgot-password on first login):")
    for email, pw in snap["passwords"].items():
        print(f"  {email}: {pw}")
    print("\nDONE. Next: deploy backend+frontend, set the Pages env vars, "
          "and send the users their temporary passwords.")


def verify(conn) -> None:
    if not SNAPSHOT_PATH.exists():
        die(f"no snapshot at {SNAPSHOT_PATH}")
    snap = json.loads(SNAPSHOT_PATH.read_text())
    after = snapshot_counts(conn, list(snap["mapping"].values()))
    ok = True
    for old_id, new_id in snap["mapping"].items():
        for table in USER_TABLES:
            b = snap["before"][old_id][table]
            a = after[new_id][table]
            status = "OK" if a == b else f"MISMATCH (before={b})"
            if a != b:
                ok = False
            print(f"  {table:20s} {new_id[:8]}…: {a} {status}")
    print("verify: OK" if ok else "verify: FAILED")
    sys.exit(0 if ok else 1)


def reverse(conn) -> None:
    """Restore the OLD ids (and the original users rows) from the
    snapshot — a faithful undo of the whole remap."""
    if not SNAPSHOT_PATH.exists():
        die(f"no snapshot at {SNAPSHOT_PATH} — cannot reverse")
    snap = json.loads(SNAPSHOT_PATH.read_text())
    backup = snap.get("users_backup")
    if not backup:
        die("snapshot predates users_backup — cannot restore original rows; "
            "manual rollback from the pre-cutover pg_dump is required")
    trans = conn.begin()
    try:
        for old_id, new_id in snap["mapping"].items():
            row = backup[old_id]  # the ORIGINAL columns, hash included
            conn.execute(
                text("INSERT INTO users (id, email, hashed_password, is_active, "
                     "is_superuser, is_verified, display_name, token_version, "
                     "created_at) VALUES (:id, :email, :hashed_password, "
                     ":is_active, :is_superuser, :is_verified, :display_name, "
                     ":token_version, :created_at)"),
                {**row, "id": old_id})
            for table in USER_TABLES:
                conn.execute(
                    text(f"UPDATE {table} SET user_id = :o WHERE user_id = :n"),
                    {"o": old_id, "n": new_id})
            conn.execute(text("DELETE FROM users WHERE id = :n"), {"n": new_id})
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    print("reversed to pre-cutover ids (original rows restored). NOTE: the "
          "Supabase identities still exist — delete them in the dashboard "
          "(Authentication -> Users) when rolling back fully.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually execute (default: plan only)")
    ap.add_argument("--plan", action="store_true", help="dry-run listing (default)")
    ap.add_argument("--verify", action="store_true",
                    help="re-check row counts against the snapshot (post-cutover)")
    ap.add_argument("--reverse", action="store_true",
                    help="restore OLD ids from the snapshot (undo)")
    ap.add_argument("--keep-supabase", action="store_true",
                    help="on failure, leave created Supabase users alone")
    ap.add_argument("--max-users", type=int, default=5,
                    help="refuse above this many accounts (expect 2)")
    args = ap.parse_args()

    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        die("SUPABASE_URL / SUPABASE_SERVICE_KEY missing from backend/.env")

    print(f"database: {DATABASE_URL.split('@')[-1]}")
    print(f"supabase: {settings.SUPABASE_URL}")

    with engine.connect() as conn:
        users = fetch_local_users(conn)
        if len(users) > args.max_users:
            die(f"{len(users)} active users (max {args.max_users}) — refusing; "
                "raise --max-users deliberately if this is expected")
        if not users:
            die("no active users found — nothing to cutover")

        for u in users:
            counts = {t: conn.execute(
                text(f"SELECT count(*) FROM {t} WHERE user_id = :z"),
                {"z": u["old_id"]}).scalar() for t in USER_TABLES}
            print(f"  {u['email']}: {u['old_id'][:8]}… -> "
                  + ", ".join(f"{t}={n}" for t, n in counts.items()))

        if args.verify:
            verify(conn)
            return

        if not args.yes:
            print("\nPLAN ONLY (no --yes): would create Supabase users for the "
                  "above and remap all six user_id tables in one transaction.")
            return

        if args.reverse:
            reverse(conn)
            return

        cutover(conn, users, args)


if __name__ == "__main__":
    main()
