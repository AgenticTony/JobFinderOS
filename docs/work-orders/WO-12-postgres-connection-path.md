# WO-12 — Postgres connection path (DECIDED)

> Priority: **P0** · Depends on: WO-11 (done) · Status: **decided
> 2026-08-27** — this work order's deliverable IS the decision and its
> plumbing; no runtime code changes (the connection does not exist until
> MIG-WO1 provisions Supabase).

## The constraint

Render has **no outbound IPv6** (ARCHITECTURE F1); Supabase's direct
connection is IPv6-by-default (IPv4 is a Pro-only paid add-on). Both
Supavisor pooler modes are IPv4-only on all tiers. Therefore: **the
direct connection string cannot work from Render at all** — the choice
is pooler mode, not direct vs pooled.

## The decision

**Supavisor SESSION mode** (pooler port 5432) for the API and the
worker.

Connection shape (fill from the Supabase dashboard when MIG-WO1
provisions the project):

```
DATABASE_URL=postgresql+psycopg://postgres.{project-ref}:{password}@aws-0-{region}.pooler.supabase.com:5432/postgres
```

### Why session mode

1. **Prepared-statement-safe.** psycopg 3 uses server-side prepared
   statements by default after `prepare_threshold` executions; session
   mode supports them, transaction mode explicitly does not (Supabase
   docs) — the same constraint that killed asyncpg (F2/WO-11).
2. **Correct for our process shape.** A persistent API server + a
   long-lived worker hold sessions naturally; session mode's
   one-client-one-backend model is what they want. Transaction mode
   exists for serverless-per-request patterns we do not have.
3. **Free, IPv4, Render-compatible.**

### Rejected alternatives (recorded so they aren't re-litigated)

| option | why not |
|---|---|
| Supavisor transaction mode (6543) | No prepared statements — requires `prepare_threshold=0` workarounds; solves a serverless problem we don't have. Documented fallback ONLY if the API ever goes per-request serverless |
| Direct connection | IPv6-only; Render has no IPv6 egress — cannot work (F1) |
| IPv4 add-on | Pro plan and above, paid — buys nothing the free pooler doesn't |
| Host on IPv6-capable infra (Fly.io) | A hosting migration to avoid a free pooler that is technically correct for our shape — upside-down |

## Acceptance criteria

1. Decision recorded with rationale and rejected alternatives (this
   file). ✓
2. `.env.example` carries the session-mode template with guidance. ✓
3. ARCHITECTURE F1 marked DECIDED, pointing here. ✓
4. MIGRATION MIG-WO1 references the path. ✓
5. No code path can produce a direct (non-pooler) Supabase connection:
   the app only ever reads `DATABASE_URL`; with WO-11 the driver is
   psycopg on both engines — no asyncpg pooler hazards remain. ✓
