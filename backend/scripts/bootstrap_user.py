"""Bootstrap: create the first account and claim the existing single-user data.

Phase 1b moved every personal row behind user_id. Existing local data (your
CV, matches, drafts, applications) has user_id=NULL. This script:
  1. creates an account (email + password) — fastapi-users hashing
  2. stamps every profile/match/draft/application row with its user_id
  3. refuses to run twice (idempotent by email)

Run from backend/:
  .venv/bin/python scripts/bootstrap_user.py you@example.com 'YourPassword!'

(Password arrives via argv for local bootstrap only; interactive prompt
version when accounts move behind the login UI.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Application,
    ApplicationDraft,
    MatchResult,
    Profile,
    User,
)


async def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    email, password = sys.argv[1].strip().lower(), sys.argv[2]

    init_db()
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"User {email} already exists (id={existing.id}) — nothing to do")
            return 0

        # fastapi-users password hashing via UserManager.create
        from fastapi_users.password import PasswordHelper

        user = User(email=email, hashed_password=PasswordHelper().hash(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created user {email} (id={user.id})")

        stamped = {
            "profiles": db.query(Profile)
            .filter(Profile.user_id.is_(None))
            .update({"user_id": user.id}),
            "match_results": db.query(MatchResult)
            .filter(MatchResult.user_id.is_(None))
            .update({"user_id": user.id}),
            "application_drafts": db.query(ApplicationDraft)
            .filter(ApplicationDraft.user_id.is_(None))
            .update({"user_id": user.id}),
            "applications": db.query(Application)
            .filter(Application.user_id.is_(None))
            .update({"user_id": user.id}),
        }
        db.commit()
        for table, count in stamped.items():
            print(f"  stamped {count:4d} rows in {table} -> user {user.id}")
        print("Done. Log in at /login with this account.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
