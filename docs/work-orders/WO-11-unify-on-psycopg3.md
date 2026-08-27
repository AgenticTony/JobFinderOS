# WO-11 — Drop asyncpg; unify on psycopg3

> Priority: **P0** · Depends on: nothing · Status: executed 2026-08-27
> Fixes ARCHITECTURE F2. Blocks WO-03 (Supabase migration) and WO-07
> (deploy) — nothing deploys while the auth layer runs a driver that
> fails on both Supabase poolers.

## The defect

`core/database.py` runs two engines: sync `psycopg` for the app, async
`asyncpg` for the fastapi-users auth layer (its SQLAlchemy adapter is
async-only). Supabase's docs state transaction mode "does not support
prepared statements" and asyncpg uses them by default; open issue
#39227 reports asyncpg failing on BOTH poolers (DuplicatePreparedStatementError
on transaction mode, timeouts on session mode under burst) with every
mitigation tried.

## The fix

SQLAlchemy 2.0's `postgresql+psycopg://` dialect serves BOTH
`create_engine` and `create_async_engine` — one driver covers the sync
app and the async auth layer. psycopg 3.3.4 is already pinned.

- `async_database_url()`: the `postgresql+asyncpg://` translation is
  gone — a Postgres URL resolves to `postgresql+psycopg://` for BOTH
  engines. The sqlite → aiosqlite translation stays (local/tests).
- `asyncpg` removed from `requirements.txt` and `requirements.lock`.

## Acceptance criteria

1. No `asyncpg` anywhere in the repo (grep-provable).
2. `async_database_url` never emits `+asyncpg`; a guard test asserts
   the async engine URL carries the psycopg driver — protecting the
   Supavisor compatibility the swap exists for.
3. The auth roundtrip (register → login → /users/me) passes through the
   async engine on psycopg on the Postgres CI leg — live proof on the
   production-target database engine.
4. sqlite/aiosqlite behaviour unchanged (local + tests).
5. Tests written first, seen red; revert-check; full suite green on
   both CI legs.
