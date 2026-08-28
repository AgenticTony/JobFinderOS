#!/usr/bin/env python3
"""MIG-WO1 data migration: SQLite -> Supabase Postgres.

Reproducible migration with the re-score script's discipline: reads from
a pre-migration SNAPSHOT (never the live DB), writes to the destination,
verifies row counts and queue invariants, and fixes sequences (bulk
INSERTs with explicit IDs don't advance them on Postgres).

Usage (from backend/):
    .venv/bin/python ../ops/migrate_sqlite_to_supabase.py \
        /path/to/snapshot.db

Prerequisites:
  - DATABASE_URL points at the destination Postgres
  - alembic upgrade head has been run on the destination
  - the snapshot is a verified copy (use sqlite3 .backup)
"""

import sqlite3
import sys
import uuid as _uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("DEBUG", "true")

from sqlalchemy import create_engine, text  # noqa: E402

TABLES = ["users", "profiles", "job_postings", "match_results",
          "application_drafts", "applications", "scrape_runs"]

# ONLY true-Boolean columns in the Postgres schema (verified against the
# models — profiles.remote_ok etc are Integer columns, NOT booleans)
BOOLS = {
    "users": {"is_active", "is_superuser", "is_verified"},
    "profiles": set(),
    "job_postings": set(),
    "match_results": set(),
    "application_drafts": {"fabrication_blocked"},
    "applications": set(),
    "scrape_runs": set(),
}
UUID_COLS = {"users": {"id"}, "profiles": {"user_id"},
             "match_results": {"user_id"}, "application_drafts": {"user_id"},
             "applications": {"user_id"}}


def main(snapshot_path: str, force: bool = False) -> int:
    from dotenv import load_dotenv
    # env WINS over .env for the destination (review finding: override=True
    # stomped an explicit DATABASE_URL=... on the command line, making it
    # impossible to target a scratch database for a rehearsal run)
    load_dotenv(BACKEND / ".env", override=False)
    dst_url = os.environ["DATABASE_URL"]
    # route through the ONE normalizer (WO-11's fifth call site rule):
    # a bare postgresql:// URL resolves to psycopg2 (not installed) here
    # exactly as it would in the app — this must not be a sixth path
    sys.path.insert(0, str(BACKEND))
    from app.core.dburl import normalize_postgres_url
    dst_url = normalize_postgres_url(dst_url)

    src = sqlite3.connect(snapshot_path)
    src.row_factory = sqlite3.Row

    data = {}
    for t in TABLES:
        rows = [dict(r) for r in src.execute(f"SELECT * FROM {t}")]
        bools, uuids = BOOLS.get(t, set()), UUID_COLS.get(t, set())
        for r in rows:
            for b in bools:
                if r.get(b) is not None:
                    r[b] = bool(r[b])
            for u in uuids:
                v = r.get(u)
                if v and isinstance(v, str) and "-" not in v and len(v) == 32:
                    r[u] = str(_uuid.UUID(v))
        data[t] = rows
        print(f"  read {t}: {len(rows)}")
    src.close()

    dst = create_engine(dst_url, connect_args={"sslmode": "require"})

    # Refuse to re-run against a populated database without --force
    if not force:
        with dst.connect() as conn:
            existing = conn.execute(
                text("SELECT COUNT(*) FROM users")).scalar()
        if existing > 0:
            print(f"REFUSING: destination already has {existing} users. "
                  "Re-run with --force to wipe and re-migrate.")
            return 2

    with dst.begin() as conn:
        if force:
            # --force wipes in reverse-FK order inside the SAME transaction
            # (review r2: the flag promised a wipe it never performed — the
            # INSERT loop would PK-violate and roll back with an opaque
            # error). The transaction makes this atomic: a failure leaves
            # the destination untouched.
            for t in reversed(TABLES):
                n = conn.execute(text(f"DELETE FROM {t}")).rowcount
                print(f"  wiped {t}: {n} rows")
        for t in TABLES:
            rows = data[t]
            if not rows:
                print(f"  {t}: 0 (skip)")
                continue
            cols = list(rows[0].keys())
            ph = ", ".join(f":{c}" for c in cols)
            cl = ", ".join(cols)
            for r in rows:
                clean = {c: (None if r[c] == "" and c != "status" else r[c])
                         for c in cols}
                conn.execute(text(f"INSERT INTO {t} ({cl}) VALUES ({ph})"),
                             clean)
            print(f"  wrote {t}: {len(rows)}")

    # fix sequences (bulk INSERTs with explicit IDs don't advance them)
    with dst.begin() as conn:
        seqs = conn.execute(text("""
            SELECT seq.relname, tbl.relname FROM pg_class seq
            JOIN pg_depend dep ON dep.objid = seq.oid AND dep.deptype = 'a'
            JOIN pg_class tbl ON tbl.oid = dep.refobjid
            JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
            WHERE ns.nspname = 'public' AND seq.relkind = 'S'
        """)).fetchall()
        for seq, tbl in seqs:
            conn.execute(text(
                f"SELECT setval('{seq}', "
                f"COALESCE((SELECT MAX(id) FROM {tbl}), 0) + 1, false)"))
        print(f"  sequences fixed: {len(seqs)}")

    print("\n=== VERIFICATION ===")
    all_ok = True
    with dst.connect() as conn:
        for t in TABLES:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            exp = len(data[t])
            ok = n == exp
            if not ok:
                all_ok = False
            print(f"  {t}: {n}/{exp} {'OK' if ok else 'MISMATCH'}")
        invariants = [
            ("v1 sub-threshold no dismissal",
             "SELECT COUNT(*) FROM match_results "
             "WHERE score<25 AND dismissed_reason IS NULL"),
            ("v2 strong wrongly dismissed",
             "SELECT COUNT(*) FROM match_results "
             "WHERE score>=25 AND dismissed_reason='below_threshold'"),
            ("v3 strong dismissed no decision",
             "SELECT COUNT(*) FROM match_results "
             "WHERE score>=25 AND dismissed_reason='below_threshold' "
             "AND decision IS NULL"),
        ]
        for name, sql in invariants:
            v = conn.execute(text(sql)).scalar()
            if v:
                all_ok = False
            print(f"  {name}: {v} {'OK' if v == 0 else 'VIOLATION'}")

    print(f"\n{'MIGRATION VERIFIED' if all_ok else 'MIGRATION FAILED'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", help="path to the pre-migration SQLite snapshot")
    ap.add_argument("--force", action="store_true",
                    help="wipe destination tables and re-migrate (destructive)")
    args = ap.parse_args()
    sys.exit(main(args.snapshot, force=args.force))
