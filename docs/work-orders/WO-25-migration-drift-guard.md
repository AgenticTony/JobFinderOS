# WO-25 — Migration drift guard: a branch migration must never strand main

> Priority: P1 · Depends on: — · Status: built 2026-09-29 (all code layers); owner steps open — see execution record
> Origin: 2026-09-28 hunt-cron outage. (WO-24 is held by the ad-URL
> ingest sketch — not yet written up — so this takes the next number.)

## Why this exists

On 2026-09-28 every `jobfinderos-hunt` run died at boot:

```
alembic.util.exc.CommandError: Can't locate revision identified by 'd94f2a6c8e1b'
```

`d94f2a6c8e1b` is the WO-19b work-rights migration. It existed only on
`feat/wo19b-work-rights-eligibility` (PR #116, open), yet the production
database was stamped at it and carried its columns
(`profiles.work_rights`, `match_results.eligibility`,
`match_results.eligibility_note`). Something built from the branch ran
`init_db()` → `alembic upgrade head` against production. Every
main-built process then called `upgrade head` from `b7e2d4f8a1c3`'s
world, could not resolve the database's revision, and exited 1.

Resolved the same day by merging #116 (main gained the file; `upgrade
head` became a no-op) and redeploying both services. The class is still
open:

- **Boot migrations run from any build.** `init_db()`
  (`backend/app/core/database.py`) applies `upgrade head` on every
  Postgres boot, whatever branch the image came from. A branch deploy
  (manual Render deploy, `autoDeploy: false`) or a local
  `alembic upgrade` with the production URL writes production schema
  ahead of main.
- **Nothing detects the drift.** The failure surfaced only because the
  owner read cron logs. Sentry is off in production (`SENTRY_DSN`
  unset), and the crash is a raw alembic traceback that names a hash,
  not the cause.
- **The blast radius is every service on main.** The API survives until
  its next deploy/restart; the cron dies on its next scheduled run.
  Hunts were missed until the merge.

Exact mechanism of the branch write is **unconfirmed** (most likely a
manual branch deploy of `jobfinderos-api`; a local run is possible).
The design covers both.

## Design — three layers, each catches what the others can't

### 1. Prevent: boot migrations apply only from the deploy branch

In `init_db()`'s Postgres path, before `command.upgrade`:

- Compute pending revisions (current DB revision → script head).
- If there are pending revisions AND the process runs on Render
  (`RENDER` env set) AND `RENDER_GIT_BRANCH != MIGRATION_BRANCH`
  (config, default `main`) → **refuse to migrate**: log a clear error
  naming the branch, the pending revisions, and the fix; exit non-zero.
  No pending revisions → boot normally (a branch build with no new
  migration stays deployable).
- `RENDER_GIT_BRANCH` is Render's documented build/runtime variable for
  Git-backed services — **verify it is actually present on both live
  services before relying on it** (hard lesson #4: docs differ from
  reality). If it is absent at runtime, the guard must fail CLOSED on
  Render (treat unknown branch as not-main) and say so.
- Local/CI runs (no `RENDER`) are unaffected — this layer does not claim
  to stop a laptop `alembic upgrade`; layer 2 detects that.

### 2. Detect: CI asserts production's revision is known to main

A new CI job (`migration-drift`) that reads the production
`alembic_version` and asserts it is a revision in the checked-out
script directory's graph (at or behind head — ahead/unknown fails).

- Runs on: `push` to main, `pull_request` targeting main, and a
  `schedule` (every 6h) so drift created outside Git is caught between
  merges.
- On a PR, the question is "would merging this strand production?" —
  production's revision must resolve in the PR head's graph.
- Failure message names the unknown revision and, if found, the
  remote branch that contains it (`git branch -r --contains` on the
  commit that introduced the file) — the outage took a manual search
  to find that.

**Credentials — owner decision required (touches production access):**
- A dedicated Postgres role, `ci_revision_reader`, with `SELECT` on
  `public.alembic_version` ONLY. No other grants, never `anon`/
  `authenticated` (house rule, CLAUDE.md). Created by migration +
  `rls_sql.py`-style idempotent SQL, or once by hand and documented.
- Connection string in a GitHub Actions secret
  (`PROD_REVISION_READER_URL`). Secrets are not exposed to fork PRs —
  acceptable (solo repo); the job must SKIP, not fail, when the secret
  is absent so fork/dependabot PRs stay green.
- Alternative if the owner rejects a prod credential in CI: the check
  runs as a Render pre-deploy command instead (same assertion, no
  GitHub secret) — weaker, since it only fires on deploy, not on
  schedule.

### 3. Explain: an unknown DB revision fails with a diagnosis, not a hash

In `init_db()`, catch alembic's `CommandError`/`ResolutionError` for an
unresolvable current revision and re-raise with: the DB revision, this
build's head, and "the database is ahead of this build — a migration
from another branch was applied to this database; merge that branch or
see WO-25". Still exits non-zero (never boot on an unknown schema).

### Also: single-head assertion

CI currently never checks `alembic heads`. Two branches each adding a
migration off the same parent merge into a two-head graph, and
`upgrade head` fails at boot with "multiple heads". Add a step:
`alembic heads` must print exactly one revision. Cheap; same class.

## Not doing

- **Blocking manual branch deploys in Render.** That's a dashboard
  habit, not code; layer 1 makes such a deploy harmless for schema.
- **Moving migrations to a separate pre-deploy step.** Worth it later
  (WO-04 split already isolates the worker), but it changes deploy
  topology; this WO keeps the boot-migration model and fences it.
- **Auto-downgrading or re-stamping production.** Downgrade drops the
  columns and their data; re-stamp makes the DB lie about its schema.
  Both are manual, owner-run recoveries only.

## Acceptance criteria (red-first, per CLAUDE.md standards 2 and 5)

1. **Branch guard refuses:** with `RENDER=true`,
   `RENDER_GIT_BRANCH=feat/x`, and a pending migration, `init_db()`
   exits non-zero, applies NOTHING (assert `alembic_version` unchanged
   and the new column absent — the outbound artifact is the schema), and
   the log names the branch and the pending revision.
2. **Branch guard allows the no-op:** same env, zero pending revisions
   → boot succeeds.
3. **Main migrates:** `RENDER_GIT_BRANCH=main` + pending → applied.
4. **Fail-closed on missing var:** `RENDER=true`, `RENDER_GIT_BRANCH`
   unset, pending → refused, log says the branch is unknown.
5. **Local unaffected:** no `RENDER` → current behavior, byte-identical.
6. **Diagnosis:** a DB stamped at a revision absent from the script dir
   → error message contains both revisions and the "ahead of this
   build" explanation; exit non-zero.
7. **Drift job red:** point the check at a CI Postgres stamped with a
   fabricated revision → job fails with the revision in the message.
   Stamped at head or an ancestor → passes. Secret absent → skipped.
8. **Single head:** a fixture migration creating a second head fails
   the heads step.
9. **Revert-proof each guard:** remove the guard, watch its test go red
   with a message naming the blast radius, restore.
10. **Live verification:** `RENDER_GIT_BRANCH` observed on BOTH live
    services (API + hunt cron) — log it once at boot (branch + short
    commit, no secrets). The first scheduled drift-job run is green
    against production.

## Owner steps (not buildable by a session)

- Approve the CI-credential approach (layer 2) or pick the pre-deploy
  alternative.
- Create `ci_revision_reader` on Supabase + add the GitHub secret.
- Set `jobfinderos-api`'s deploy branch back to `main` if it isn't
  (the 2026-09-28 follow-up).
- Consider setting `SENTRY_DSN` on both services: this outage paged
  nobody.

## Execution record — 2026-09-29

Branch `feat/wo25-migration-drift-guard`. All three code layers plus the
single-head check are built; the owner steps below are what's left.

**Layer 1 + 3 (boot):** `app/core/migration_guard.py` (pure: revision
state, branch rule, single head, diagnosis) + `database._guarded_upgrade`,
now the ONLY place `init_db` calls `command.upgrade` (all three paths:
Postgres under the advisory lock, fresh SQLite, legacy SQLite). The stamp
is read AFTER the lock is taken, so a concurrent winner's upgrade is
visible. `MIGRATION_BRANCH` setting (default `main`). Boot logs
`Deploy build: <branch>@<sha7>` on Render.

**Layer 2 (CI):** `scripts/check_migration_drift.py` +
`.github/workflows/migration-drift.yml` (push main, PRs into main, every
6h, manual; `contents: read`). **Design change found while building:**
`alembic_version` carries RLS with NO policies (`rls_sql.py`
`LOCKED_SERVICE_TABLES`), so a plain read-only role sees ZERO rows — a
naive check would read that as "fresh DB, behind head" and PASS. The
script fails closed on zero rows, and `ops/sql/ci_revision_reader.sql`
gives the role its own `revision_reader` SELECT policy (`ensure_rls`
only ENABLEs RLS there, never drops foreign policies — verified).

**Verification:**
- `tests/test_migration_guard.py` (32) — asserts on the schema of a
  scratch DB (stamp + full column snapshot), not on return values; head
  and parent are DERIVED from the graph so future migrations need no
  edits. `test_units.py` advisory-lock tests gained a stamp-read seam and
  a new test: lock released + no upgrade on a guard refusal.
- Revert-proof: guard call removed, Postgres path bypassing the guard,
  missing branch failing open, unknown stamp undiagnosed, zero rows
  passing, unlock outside `finally` — each turned its tests red; restored.
- Real Postgres 16 (docker): branch build refused (stamp + column
  unchanged), main build migrated; reader role saw the stamp, was denied
  `profiles` and writes; dropping its policy turned the script red;
  a main boot re-ran `ensure_rls` and the policy survived; foreign stamp
  → drift exit 1 + boot diagnosis; advisory lock free after a refusal
  in-process.
- Self-review defect, fixed: the first cut read the stamp on the
  advisory-lock connection, holding ACCESS SHARE on alembic_version for
  the whole migration — a migration (or ensure_rls) needing ACCESS
  EXCLUSIVE on that table would wait on its own boot until lock_timeout.
  Reproduced on PG16 (`LockNotAvailable`), fixed by reading on a
  short-lived connection, probe re-run green, pinned by the advisory-lock
  unit test (revert-checked).
- Suites after the fix: 576 passed / 14 skipped (SQLite, 4 clean runs),
  588 / 2 (Postgres 16), flow test green. One earlier SQLite run showed
  18 failures that never reproduced (including under the same parallel
  load) — most likely another session sharing `backend/test_suite.db`
  (CLAUDE.md rule 7); unexplained, recorded rather than dismissed.

**Deviation:** the single-head check lives in the drift workflow (runs
even without the secret) and in the pytest suite
(`TestSingleHead::test_repo_graph_has_one_head`, so ci.yml's backend job
enforces it too) rather than as a separate ci.yml step.

**Owner steps (not buildable by a session):**
1. After this deploys: confirm the boot log shows
   `Deploy build: main@<sha>` on BOTH `jobfinderos-api` and
   `jobfinderos-hunt`. Render documents `RENDER_GIT_BRANCH` at runtime
   for all service types; if the log says `<unknown branch>`, the next
   migration from main will be REFUSED (fail-closed) — fix before merging
   any migration. This PR ships no migration, so its own deploy boots
   either way.
2. Run `ops/sql/ci_revision_reader.sql` in the Supabase SQL editor
   (fresh password), add `PROD_REVISION_READER_URL` as a GitHub Actions
   secret, then run the workflow manually: expect `OK … at head`.
   Until then the job runs the single-head check and skips the read.
3. GitHub disables scheduled workflows after 60 days without repo
   activity — the push/PR triggers still run; re-enable if it lapses.
4. Set `jobfinderos-api`'s deploy branch to `main` if it isn't.
5. Consider `SENTRY_DSN` on both services — this outage paged nobody.

## Review fix — 2026-09-30: the drift check was not armed

Post-merge review: with `PROD_REVISION_READER_URL` unset, the script
treated a missing secret as "skip and pass" on EVERY trigger — all five
post-merge runs (push to main + the 6-hourly schedule, e.g. run
`36716374209`) were green while never reading production. The skip was
designed for fork/Dependabot PRs; it silently covered main too.

Fix: a missing URL now FAILS ("NOT ARMED") unless `DRIFT_CHECK_OPTIONAL`
is exactly `true`, which the workflow sets only for PRs whose head repo
is a fork or whose author is `dependabot[bot]` (keyed on the PR author,
not `github.actor`, so a human re-run of a Dependabot job still skips).
An empty or malformed flag fails closed. Tests red-first: default-fails,
exit codes per flag value, and the workflow's flag expression pinned.

**Consequence, intended:** until the owner runs
`ops/sql/ci_revision_reader.sql` and adds the secret, the "Migration
drift" workflow is RED on main, on the schedule and on same-repo PRs.
That red is the truthful state — the check cannot see production.
