"""Collapse already-scored duplicate matches (one-off, 2026-09-02).

The ManpowerGroup incident left users with the same job scored 2-3x
(Dispatcher: 90/83/68 under Experis/Manpower/Jefferson Wells). The
fuzzy gate now catches this at match time (description containment);
this script cleans the rows that predate the fix.

Per user: cluster undecided, kept matches with likely_same_job
(descriptions included), keep the highest-scored copy per cluster,
dismiss the rest as 'duplicate'. Dry-run by default; --apply writes.

Run from backend/:
    .venv/bin/python scripts/dedupe_existing_matches.py           # dry
    .venv/bin/python scripts/dedupe_existing_matches.py --apply
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import orm  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.dedupe import likely_same_job  # noqa: E402
from app.models import JobPosting, MatchResult, User  # noqa: E402


class Row:
    __slots__ = ("match", "job")

    def __init__(self, match: MatchResult, job: JobPosting):
        self.match = match
        self.job = job


def clusters(rows: list[Row]) -> list[list[Row]]:
    """Union-find over pairwise likely_same_job (n is per-user small)."""
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i].job, rows[j].job
            if likely_same_job(
                title_a=a.title, company_a=a.company, location_a=a.location,
                title_b=b.title, company_b=b.company, location_b=b.location,
                desc_a=a.description, desc_b=b.description,
            ):
                parent[find(i)] = find(j)

    groups: dict[int, list[Row]] = defaultdict(list)
    for i, row in enumerate(rows):
        groups[find(i)].append(row)
    return [g for g in groups.values() if len(g) > 1]


def pick_winner(group: list[Row]) -> Row:
    # Highest score wins; ties prefer the richer description (the full
    # ad scores truer than a snippet), then the official jobtech feed.
    def key(r: Row):
        return (
            r.match.score or 0,
            len(r.job.description or ""),
            1 if r.job.source == "jobtech" else 0,
        )

    return max(group, key=key)


def main() -> None:
    apply = "--apply" in sys.argv
    db: orm.Session = SessionLocal()
    try:
        users = [u.id for u in db.query(User.id).all()]
        total_dupe = 0
        for user_id in users:
            rows = [
                Row(m, j)
                for m, j in db.query(MatchResult, JobPosting)
                .join(JobPosting, MatchResult.job_id == JobPosting.id)
                .filter(
                    MatchResult.user_id == user_id,
                    MatchResult.decision.is_(None),
                    MatchResult.dismissed_reason.is_(None),
                )
                .all()
            ]
            for group in clusters(rows):
                winner = pick_winner(group)
                losers = [r for r in group if r is not winner]
                print(f"user {user_id} cluster:")
                print(
                    f"  KEEP   [{winner.job.source:9}] {winner.job.title[:40]!r} "
                    f"@ {winner.job.company} score={winner.match.score}"
                )
                for r in losers:
                    print(
                        f"  DROP   [{r.job.source:9}] {r.job.title[:40]!r} "
                        f"@ {r.job.company} score={r.match.score}"
                    )
                    r.match.dismissed_reason = "duplicate"
                    r.match.decision = "rejected"
                    r.match.reasoning = (
                        "Not shown: duplicate — the same job scored higher "
                        "from another board."
                    )
                    total_dupe += 1
        if apply:
            db.commit()
            print(f"\nAPPLIED: {total_dupe} duplicate matches dismissed.")
        else:
            db.rollback()
            print(f"\nDRY RUN: would dismiss {total_dupe} duplicate matches. "
                  "Re-run with --apply to write.")
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    main()
