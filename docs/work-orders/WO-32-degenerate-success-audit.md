# WO-32 — Degenerate-success audit (2026-10-30)

> Trigger: a beta tester dead-ended in onboarding step 5 — a malformed
> GLM reply parsed to `{}`, the suggest call returned empty lists as
> SUCCESS, and the wizard rendered nothing with Continue permanently
> disabled (fixed in PR #151). This audit swept the whole codebase for
> the class: **external/degenerate output converted into an empty or
> default SUCCESS that no downstream layer can distinguish from a
> legitimate empty result.**

Owner decision: fix the five live-user-facing findings immediately; file
the rest. Method: every claim verified against the code (agent
inventories were leads, not conclusions); backend fixes red-tested
first; the frontend has no component-test harness — those fixes are
verified by trace + tsc + build, and that harness gap is recorded below.

## The root pattern

Every incident in this class pairs:
1. a progress gate whose enabling condition needs fetch output
   (`Boolean(country)`, `selected.size > 0`, `status === 'ready'`,
   `parsed.get("score")`), with
2. a failure mode that produces neither data nor error (silent
   `catch → null`, infinite swallow-poll, `{}`-on-unparseable,
   timeout-less spinner).

The remedy has two halves, and every fix below implements both:
**treat no-data-with-no-error as an error state, and keep a manual
fallback reachable while waiting.**

## Findings and dispositions

### Fixed in this work order

| # | Finding | Fix |
|---|---|---|
| F1 | Wizard steps 1–2: `/geo` failure = zero country buttons, Continue locked, no error, no retry — every first-run user's path (`OnboardingWizard.tsx:267` silent catch) | geo failure is its own state with a "Try again" button (steps 1 and 2) |
| F2 | `extract_profile` capped at 2000 output tokens with no `finish_reason` check — long CVs deterministically truncate to `{}` and surface as a repeatable "re-upload your PDF" error that blames a fine file (the tailor draft-39 mechanism) | truncation raises with its real cause; `fail_extract` surfaces ValueError detail. Cap stays 2000 (2026-10-16 owner decision) — if truncation is observed live, a named `EXTRACT_MAX_TOKENS` is the knob |
| F3 | `waitForAiJob` swallowed ALL poll errors — a pruned/persistently-5xx job = infinite 2s poll; the wizard's manual `+` input was hidden while loading, so Continue was locked with no escape | 5 consecutive poll failures throw (callers catch and show it); `followExtraction` gained the catch it never had (an unhandled rejection was waiting); the `+` input stays reachable during the spinner |
| F4 | Sent email silently shipped 2 of 3 PDFs: CV read failure → attachment dropped, `status="sent"`, no trace (`storage.read_cv_at_path` → None; BOTH senders: `draft_service._send_with_pdfs` and `apply_service._send_email_application` — the latter would send ZERO attachments, no CV at all) | an unreadable original CV raises before dispatch: failed, retryable, nothing sent. Reverses the old "missing CV must never kill a send" rule — the user approves THREE attachments; shipping fewer is an outbound lie. Note: a draft whose CV snapshot was retired by a re-upload now fails loudly; re-preparing the application is the honest path |
| F5 | Composio outage read as "not connected": `list_connections` returned `[]` on API error; Settings showed "not available", the scan endpoint said "connect your Gmail", background scans silently skipped | `list_connections` returns `None` = UNKNOWN (≠ `[]` = definitively none); Settings gains `reachable` + an explicit "status unknown — try again" state; scan returns 503 during outage; background scan logs the skip |

Also fixed here (found by the same sweep, shipped first): a *partial*
extract reply (parses but omits keys) applied replace-semantics and
nulled every omitted profile field — the AI-11 wipe one level shallower
(PR #152, `_extraction_complete`).

### Filed (not fixed — lower tier)

- FE: DraftCard orphaned in `queued`/`drafting` spins forever (no retry while pending; recovered only if the backend flips the status). Backend-dependent, low likelihood.
- FE: stats-endpoint-only failure renders HuntPulse all-zeros silently (loadFailed covers only list endpoints).
- FE: ProfileView hides all sections on non-404 profile fetch failure, no error.
- FE: `ResetPasswordView` `getSession().then` has no `.catch` — UNVERIFIED whether supabase-js v2 can reject here; check before touching.
- FE: `OnboardingWizard.tsx` Continue guard `step === 3 && loadingQueries` is a stale index (fetch runs at step 4) — the intended guard never fires where designed.
- BE D4: partial fetch (`fetch_complete=False`) is honored only for jobtech (watermark held); careerjet/reed/adzuna partial runs land `completed` with undercounted `jobs_found`, no marker. Self-heals next run; lost signal, not lost jobs.
- BE D5: match replies with a valid score but missing prose keys store defaulted `reasoning/recommendation/confidence` with no marker. Rare; score is validated.
- BE D6: judge at 2000 tokens: a heavily-fabricated doc's long `unsupported` list truncates → `{}` → ValueError → draft fails with a transport-style message instead of the fabrication-block verdict. Fail-closed, wrong message.
- BE D7: `_retire_replaced_cv` swallows storage-delete failure — the replaced CV object orphans and outlives GDPR erasure (erasure deletes only the current path + snapshots).
- BE D8: PDF unicode-font load failure → latin-1 `replace` → non-latin-1 chars (pl/cs/tr/et; åäö survive) become `?` in employer-bound PDFs. Deployment-gated.
- BE D9: a content-deterministic malformed match reply keeps its job `new` forever — spend re-burned every hunt, never surfaced. Deferral, not verdict; unbounded per poisoned job.

### Verified safe (no action)

All five `_parse_json` consumers (match AI-10, tailor AI-9/14, judge
fail-closed, extract AI-11 + `_extraction_complete`, suggest #151);
every queue `Outcome("done")` path (a guard block is a result, not a
swallow); scraper exception vs empty-source (ScrapeRun failed + error);
worker per-user match failures (self-healing, counted); GDPR export CV
read (logged best-effort, text-first by design); 11 further
swallow-paths inventoried as observability-only or fail-closed.

## Coverage rule going forward

Every AI-reply consumer has a malformed-reply test that asserts the
OBSERVABLE outcome is a failure, never a silent empty success (match,
tailor, judge, extract ×2, suggest ×3). The gap that let the suggest bug
ship was that tests fed the AI clean replies or raised exceptions —
never garbage that parses to nothing. New AI kinds must add the
equivalent test in the same commit.

## Known gaps

- No frontend component-test harness: F1/F3/F5-frontend are verified by
  trace + tsc + build + i18n tests only. A component harness (even a
  minimal react-render smoke) would make this class testable.
- F2 likelihood is model-behavior-dependent; the mechanism is verified,
  live reply sizes are not measured.
