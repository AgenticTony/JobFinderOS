# MIG-WO2 cutover runbook — fastapi-users → Supabase Auth

> Status: code complete on `mig/wo2-supabase-auth` (backend, frontend,
> cutover script, tests green). This runbook is the live-cutover
> checklist — the steps that touch the Supabase dashboard, the live
> database, and the deploys. Work through it top to bottom; the
> order matters.

## What changed (context for the steps)

- The backend no longer owns accounts: it verifies Supabase's ES256
  access tokens against the project JWKS (`backend/app/users.py`) and
  mirrors the user row locally on first request.
- The frontend talks to Supabase directly (`@supabase/supabase-js`,
  PKCE): login, signup, forgot/reset. Password reset — the thing
  WO-10 existed for — is live as `/login?mode=forgot` → email link →
  `/reset-password`.
- fastapi-users, the async auth engine, AUTH_SECRET, the auth rate
  limits and `scripts/bootstrap_user.py` are deleted.

## Pre-flight (one-time dashboard setup — do these FIRST)

1. **Custom SMTP (BLOCKER for real emails).** The Supabase built-in
   sender is 2 messages/hour AND team-addresses-only (docs-verified
   2026-09-08) — confirmation/reset emails to beta testers will not
   deliver. In the dashboard: Authentication → Emails → SMTP Settings,
   configure Resend's SMTP (host `smtp.resend.com`, port 465, user
   `resend`, password = the Resend API key already in backend/.env,
   sender `JobFinderOS <noreply@…>` on a Resend-verified domain).
   Starting limit 30/hour; raise it on the Rate Limits page if the
   beta grows. Resend docs: resend.com/docs/dashboard/domains + the
   SMTP integration guide.
2. **Redirect URLs.** Authentication → URL Configuration: set
   - Site URL: `https://jobfinderos.pages.dev` (or the custom domain)
   - Redirect URLs: the Site URL, `https://jobfinderos.pages.dev/**`,
     `/reset-password` + `/sv/reset-password` (both locales!), and
     `http://localhost:3000/**` for local dev.
3. **Copy the publishable (anon) key.** Project Settings → API Keys →
   the `anon public` / publishable key. NEVER the service-role key —
   it must not reach the browser bundle.
4. **Backups.** `bash ops/backup.sh` (pg_dump) + verify the dump
   (`ops/restore.sh` has the verify steps). The cutover script also
   writes its own rollback snapshot, but the pg_dump is the belt to
   that braces. This also closes the standing "restore rehearsal"
   open item if you walk the restore once.

## Cutover (a few minutes, between hunt windows)

The hunts run 06:00/18:00 UTC — run this mid-window so no worker is
mid-hunt against the user ids being remapped (the remap itself is one
transaction, sub-second).

5. **Dry-run first** (read-only, safe anytime):
   ```bash
   cd backend && .venv/bin/python ../ops/mig_wo2_cutover.py --plan
   ```
   Expect 3 accounts (tonyforan007, foranmarketing, lorna_co_za) with
   their per-table counts.
6. **The cutover** (creates Supabase identities + remaps FKs in one
   transaction, verifies counts, prints one-time temp passwords):
   ```bash
   cd backend && .venv/bin/python ../ops/mig_wo2_cutover.py --yes
   ```
   Copy the printed passwords out of the terminal WHEN YOU SEE THEM —
   they are printed once and stored NOWHERE (the snapshot file is
   gitignored and deliberately contains no passwords). Post-check:
   `--verify`.
7. **Deploy backend** — merge the branch, Render deploys it. Remove
   stale env vars in the Render dashboard if the blueprint sync leaves
   them (AUTH_SECRET, TRUST_PROXY_HEADERS — the blueprint no longer
   declares them; if Settings still shows them from before, delete —
   extra inputs are boot-fatal).
   NOTE: render.yaml now declares `SUPABASE_URL` for the worker too —
   confirm the prompt for it on sync.
8. **Deploy frontend**:
   ```bash
   export NEXT_PUBLIC_SUPABASE_URL=https://jsibogzklhswpmozcyhn.supabase.co
   export NEXT_PUBLIC_SUPABASE_ANON_KEY=<the publishable key>
   bash ops/deploy_frontend.sh
   ```
9. **Hand over passwords**: send each user their temp password; they
   sign in and (advisedly) change it via Forgot password — which also
   exercises the new flow end-to-end.

## Verification (do these before calling it done)

- [ ] Signup on the Pages URL delivers a confirmation email (custom
      SMTP) and the link lands signed-in on `/app`
- [ ] Forgot-password delivers the reset email; the link opens
      `/reset-password` and sets a new password
- [ ] An old fastapi-users password (pre-cutover) FAILS — hashes were
      never migrated; only the Supabase passwords work
- [ ] Sign-in → console → existing matches/drafts/applications all
      present (the FK remap preserved them)
- [ ] `DELETE /api/v1/account/delete` (Settings → delete account on a
      THROWAWAY account) removes the Supabase identity too
- [ ] Hunt pulse counts unchanged (funnel reads the remapped rows)

## Rollback

- Database + identities: `--reverse --yes` restores the pre-cutover
  UUIDs AND the original users rows from the snapshot (then delete the
  Supabase identities in the dashboard).
- Code: revert the deploys to the previous release (Render keeps
  history; Pages: redeploy the previous `out/`). The branch's commits
  stay for a retry.

## Open follow-ups (not blockers)

- `users.token_version` column: dead weight kept for rollback safety;
  drop in a later migration once MIG-WO2 is stable in production.
- Supabase Auth's own protections: HIBP leaked-password checks are
  Pro-plan-only; auth rate limits are per-IP by default (both
  documented in MIGRATION.md's doc-verified facts).
