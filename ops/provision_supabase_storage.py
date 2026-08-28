#!/usr/bin/env python3
"""WO-07: provision the private Supabase Storage bucket for CVs.

Render's free-tier filesystem is EPHEMERAL (render.com/docs/free — local
changes are lost on redeploy, restart, and spin-down), so production runs
STORAGE_BACKEND=supabase and every CV upload must land in Supabase Storage.
This script is the one-off, IDEMPOTENT provisioning step: it creates the
private bucket `cvs` via the officially documented REST API
(supabase.com/docs/reference/api/create-a-bucket) and verifies it is NOT
public (CVs are PII — app/services/storage.py serves reads with the
service key, never public links).

Usage (from backend/, creds in .env or environment):
    .venv/bin/python ../ops/provision_supabase_storage.py

Requires: SUPABASE_URL, SUPABASE_SERVICE_KEY (service role, from the
Supabase dashboard → Settings → API).
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import os  # noqa: E402


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env", override=False)  # env wins over .env
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "cvs")
    if not url or not key:
        print("REFUSING: SUPABASE_URL / SUPABASE_SERVICE_KEY not set "
              "(service-role key, Supabase dashboard → Settings → API)")
        return 2

    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}

    with httpx.Client(timeout=30) as client:
        # Idempotency: check current state first (GET /storage/v1/bucket —
        # supabase.com/docs/reference/api/list-buckets)
        existing = client.get(f"{url}/storage/v1/bucket", headers=headers)
        existing.raise_for_status()
        names = {b["name"]: b for b in existing.json()}

        if bucket in names:
            already = names[bucket]
            if already.get("public"):
                print(f"ERROR: bucket {bucket!r} exists but is PUBLIC — CVs "
                      "are PII. Make it private in the dashboard and re-run.")
                return 1
            print(f"bucket {bucket!r}: already exists, private — nothing to do")
            return 0

        # POST /storage/v1/bucket {name, public:false} — create-a-bucket
        resp = client.post(f"{url}/storage/v1/bucket", headers=headers,
                           json={"name": bucket, "public": False})
        if resp.status_code not in (200, 201):
            print(f"create failed: HTTP {resp.status_code} {resp.text}")
            return 1

        # Verify what we actually got (never trust the request alone)
        existing = client.get(f"{url}/storage/v1/bucket", headers=headers)
        existing.raise_for_status()
        got = next((b for b in existing.json() if b["name"] == bucket), None)
        if not got:
            print(f"ERROR: create returned {resp.status_code} but bucket "
                  f"{bucket!r} is not listed — investigate before deploying")
            return 1
        if got.get("public"):
            print(f"ERROR: bucket {bucket!r} was created PUBLIC — make it "
                  "private in the dashboard and re-run")
            return 1

        print(f"bucket {bucket!r}: created, PRIVATE, id={got.get('id')}")
        print("next: set STORAGE_BACKEND=supabase on the Render services")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
