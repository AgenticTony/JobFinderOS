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
  tables, delete the old users rows. Commit; then re-verify per-table
  row counts against the pre-write snapshot. FIVE of the six carry real
  FKs to users.id (profiles, match_results, application_drafts,
  applications, feedback — all NOT NULL); only ai_usage is FK-free,
  remapped purely for cost-accounting continuity.

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
            # datetime on Postgres, str on SQLite — accept both
            "created_at": (
                r["created_at"].isoformat()
                if r["created_at"] and hasattr(r["created_at"], "isoformat")
                else r["created_at"]
            ),
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


def _relocate_user(wconn, *, from_id: str, to_id: str, insert: dict) -> int:
    """Move ONE account from from_id to to_id inside the caller's open
    write transaction. Returns the number of child rows moved.

    EMAIL PARKING (review 2026-09-08, reproduced): users.email is
    UNIQUE (ix_users_email) and the old row still owns the address when
    the new row is inserted — insert-first deletes nothing, delete-first
    orphans FK children. The old row's email is parked on a per-id
    sentinel first, which frees the UNIQUE slot for the new row without
    touching the children.
    """
    wconn.execute(
        text("UPDATE users SET email = :parked WHERE id = :o"),
        {"parked": f"migrating-{from_id}@migrating.invalid", "o": from_id},
    )
    wconn.execute(
        text("INSERT INTO users (id, email, hashed_password, is_active, "
             "is_superuser, is_verified, display_name, token_version, "
             "created_at) VALUES (:id, :email, :hashed_password, "
             ":is_active, :is_superuser, :is_verified, :display_name, "
             ":token_version, :created_at)"),
        insert,
    )
    moved = 0
    for table in USER_TABLES:
        n = wconn.execute(
            text(f"UPDATE {table} SET user_id = :n WHERE user_id = :o"),
            {"n": to_id, "o": from_id},
        ).rowcount
        moved += int(n)
        print(f"  {table}: {n} row(s) {from_id[:8]}… -> {to_id[:8]}…")
    wconn.execute(text("DELETE FROM users WHERE id = :o"), {"o": from_id})
    return moved


def cutover(conn, users, args) -> None:
    # ---- pre-write snapshot (NO passwords — they are printed once and
    # must never persist anywhere; review finding 2026-09-08) ----
    old_ids = [u["old_id"] for u in users]
    snap = {
        "_handling": ("SECRET (rollback material): contains pre-migration "
                      "password hashes. Never commit; destroy after the "
                      "cutover window. No plaintext passwords."),
        "created_at": None,
        "mapping": {},           # old_id -> new_id
        "before": snapshot_counts(conn, old_ids),
        "users_backup": backup_users_rows(conn, old_ids),  # for --reverse
    }
    temp_passwords: dict[str, str] = {}  # email -> pw, printed at the end ONLY

    # ---- phase 1: create Supabase identities ----
    created: list[str] = []
    print("\nphase 1: creating Supabase users (email_confirmed)…")
    try:
        for u in users:
            pw = secrets.token_urlsafe(18)
            new_id = supabase_create_user(u["email"], pw)
            snap["mapping"][u["old_id"]] = new_id
            temp_passwords[u["email"]] = pw
            created.append(new_id)
            print(f"  {u['email']} -> supabase id {new_id}")
    except BaseException:  # SystemExit (die) AND httpx/KeyError shape
        # failures — review round 2: catching only SystemExit orphaned
        # identities on transport errors before the snapshot was written
        if not args.keep_supabase:
            for nid in created:
                supabase_delete_user(nid)
        raise

    snap["created_at"] = utc_now().isoformat() + "Z"
    SNAPSHOT_PATH.write_text(json.dumps(snap, indent=2))
    print(f"snapshot written: {SNAPSHOT_PATH}")
    print("  HANDLE AS A SECRET: no PLAINTEXT passwords, but it carries the "
          "pre-migration password HASHES (fastapi-users rollback material) — "
          "destroy it once the cutover window closes.")

    # ---- phase 2: the one-transaction remap ----
    # engine.begin() opens a FRESH connection whose transaction starts at
    # its first statement — the read-phase SELECTs on `conn` autobegun a
    # transaction there long ago, and conn.begin() on an autobegun
    # connection raises InvalidRequestError (review finding, reproduced
    # on SQLAlchemy 2.0.52).
    print("\nphase 2: remapping local rows (single transaction)…")
    try:
        with engine.begin() as wconn:
            for old_id, new_id in snap["mapping"].items():
                cols = wconn.execute(
                    text("SELECT email, is_active, is_superuser, display_name, "
                         "created_at FROM users WHERE id = :o"),
                    {"o": old_id},
                ).mappings().one()
                _relocate_user(
                    wconn, from_id=old_id, to_id=new_id,
                    insert={"id": new_id, "email": cols["email"],
                            "hashed_password": "supabase-auth",
                            "is_active": cols["is_active"],
                            "is_superuser": cols["is_superuser"],
                            "is_verified": True,
                            "display_name": cols["display_name"],
                            "token_version": 0,
                            "created_at": cols["created_at"]},
                )
    except Exception:
        print("transaction FAILED — rolled back (engine.begin context); "
              "cleaning up Supabase users…", file=sys.stderr)
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
    print("\ntemp passwords — PRINTED ONCE, stored NOWHERE (change them via "
          "forgot-password on first login):")
    for email, pw in temp_passwords.items():
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
    snapshot — a faithful undo of the whole remap. Uses the same
    email-parking relocate + fresh-connection transaction as the
    forward cutover."""
    if not SNAPSHOT_PATH.exists():
        die(f"no snapshot at {SNAPSHOT_PATH} — cannot reverse")
    snap = json.loads(SNAPSHOT_PATH.read_text())
    backup = snap.get("users_backup")
    if not backup:
        die("snapshot predates users_backup — cannot restore original rows; "
            "manual rollback from the pre-cutover pg_dump is required")
    # Fresh connection (the read-phase SELECTs autobegun `conn`'s
    # transaction — conn.begin() would raise InvalidRequestError), same
    # email-parking relocate as the forward cutover (the post-cutover
    # row holds the UNIQUE email until it is deleted).
    try:
        with engine.begin() as wconn:
            for old_id, new_id in snap["mapping"].items():
                row = backup[old_id]  # the ORIGINAL columns, hash included
                _relocate_user(
                    wconn, from_id=new_id, to_id=old_id,
                    insert={"id": old_id, "email": row["email"],
                            "hashed_password": row["hashed_password"],
                            "is_active": row["is_active"],
                            "is_superuser": row["is_superuser"],
                            "is_verified": row["is_verified"],
                            "display_name": row["display_name"],
                            "token_version": row["token_version"],
                            "created_at": row["created_at"]},
                )
    except Exception:
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
