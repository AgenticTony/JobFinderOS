# WO-20 — Outcome layer: canonical statuses, reply detection, deadline & follow-up nudges

> Priority: P1 · Depends on: none hard (reply detection rides the
> Composio Gmail connection, proven in production 2026-08-31) ·
> Status: **executed 2026-10-15 — see execution record** ·
> **first among P1s** (2026-10-15
> re-prioritization: no competitor shows outcomes — volume metrics only;
> outcome honesty is uncontested white space — teardown §7)
> Origin: ROADMAP owns the WHY ("Launch weapon — outcome tracking";
> the one proof point no competitor has). This WO owns the HOW.
> Execution patterns adapted 2026-09-08 from `MadsLorentzen/
> ai-job-search` `/outcome` + `/gmail-sync` commands — the only
> production-grade repo surveyed; its status vocabulary and
> propose-then-confirm discipline are borrowed near-verbatim.

## Why this exists

`applications.status` (`application.py:51`) records only the SEND side
(queued/sent/error). Nothing anywhere records what the employer did.
Every outcome number on the future landing page depends on this layer,
and today the honest answer to "did anything come back?" is a manual
spreadsheet — the exact failure mode the product exists to remove.

Two facts make this cheap to build now:

1. **Replies already land in the user's inbox.** `apply_service.py:145`
   sets `reply_to` to the user's account email. For a user whose
   account email is the Gmail they connected through Composio
   (entity-scoped, live in production), employer replies are readable
   today — no new plumbing.
2. **The Composio connection layer is built and connected** (WO-07
   live verification 2026-08-31); this WO is its first read-side use.

## Design

### 1. Canonical status vocabulary (steal wholesale)

Adopted from the source repo's tracker vocabulary, extended with our
send-side states:

- **Outcome states**: `drafted | applied | interview | offer | hired |
  rejected | no_response | offer_declined | withdrawn`
- **Final** (application closed): `hired, rejected, no_response,
  offer_declined, withdrawn`. **Open**: everything else.
- `drafted` is open but distinct — nothing was sent, nobody is late
  replying; never counted as quiet, never chased.
- New columns on `applications` (additive migration):
  `outcome_status`, `outcome_source` (`manual | reply_detection`),
  `outcome_note`, `outcome_evidence` (message id / citation),
  `resolved_at`. Send-side `status` is untouched.

### 2. Reply detection (Gmail via Composio, propose-then-confirm)

For users with a connected Gmail entity, a periodic scan:

1. **Candidate set**: open applications with `sent_at` set, matched
   against inbound messages since the last processed id (incremental
   state per user — never rescans).
2. **Search**: from the application's `target_email`, the company
   domain, and common ATS sender domains (greenhouse.io, lever.co,
   myworkday.com, ashbyhq.com, smartrecruiters.com, icims.com,
   bamboohr.com).
3. **Classification reads the FULL body, never subject/snippet alone**
   — the source repo's rule, and the reason: snippets truncate the
   phrase that distinguishes "we'd like to schedule a call" from
   "thanks for applying". Classify to the vocabulary above; uncertain
   → surface, never guess.
4. **Propose-then-confirm — no silent writes.** Every classified
   change is presented as a batch with its source message cited;
   nothing touches `applications` until the user approves the batch.
   A wrong write corrupts the stats the landing page will publish;
   same spirit as invariant #1 (nothing outbound without approval).
5. **PII**: scanning reads the user's own inbox through their own
   entity-scoped connection; store only the classification + evidence
   pointer, not message bodies. Extend the privacy notice wording.

### 3. Deadline and follow-up nudges

Adapted from the source repo's outcome flow; all behind the existing
notification surfaces (no new send paths without approval gates):

- **Deadline urgency on approved-not-sent drafts** (the "Finish
  applying" cliff): 🔥 deadline within 7 days, ⚠ deadline passed. The
  failure mode to name in the UI, in one line: documents written,
  never sent, now unsendable. Our matches auto-pass at 30d — the
  deadline clock must be visible before that, not after.
- **Follow-up drafts**: sent applications 10+ days quiet with fewer
  than two follow-ups → offer a short follow-up email drafted in the
  user's voice, routed through the same draft-review-approve gates as
  any outbound artifact. Drafted rows are never chased.
- **Quiet-application sweep**: 60+ days quiet → propose batch
  `no_response` resolution (one confirmation, cited, undoable). This
  is what keeps published stats honest without user drudgery —
  silence must land in the denominator.

### 4. Stats surface (minimal)

One query per landing-page section: response rate (any non-
`no_response` employer signal / sent), interview rate, time-to-first-
response. Denominator = `sent_at IS NOT NULL`. Phase-3 landing
placeholder swaps to real numbers the day this ships.

## Sequencing inside the WO

1. Vocabulary + manual outcome markers (works for every user, no
   Composio dependency) — small, ships first.
2. Reply detection behind it (Gmail-connected users).
3. Nudges last (they consume outcomes).
4. Own-Gmail sending (CLAUDE.md open item) later makes reply coverage
   universal; this WO does not block on it.

## Acceptance criteria (red-first; tests ship in the same commit)

1. Vocabulary: Final/Open partition tests; no code path writes an
   outcome outside the enum; send-side `status` untouched by outcome
   writes (migration round-trip test).
2. Classification fixtures: "we'd like to schedule a call" →
   `interview`; "we have decided to move forward with other
   candidates" → `rejected`; ambiguous → surfaced-not-guessed. Red
   first: classification from subject alone must FAIL the
   full-body-only assertion.
3. Approval gate: no `applications` row changes without a confirmed
   batch — revert the gate, test goes red (TestOutboundIdentity
   pattern applied to the inbox direction).
4. Incremental state: a processed message is never reprocessed
   (idempotency test with replayed scan).
5. Nudges: thresholds (7d 🔥 / passed ⚠; 10d & <2 follow-ups; 60d
   sweep) asserted; `drafted` never chased, never counted quiet.
6. Stats: response-rate denominator excludes unsent applications;
   `no_response` sweep results count in the denominator.

---

## Execution record — 2026-10-15

**Status: executed** (all four layers; 48 acceptance tests in
`backend/tests/test_wo20_outcomes.py`, all green; migration round-trip
verified upgrade→head and downgrade on a scratch DB).

What shipped, by layer:

1. **Vocabulary + columns** (`20260915…` no — `20261015_f1a3c5e7b9d1`):
   `applications.outcome_{status,source,note,evidence}` + `resolved_at` +
   `follow_up_of_id`; drafts gain `follow_up_application_id`; new tables
   `outcome_proposals` (unique (application, message) — the re-scan
   dedupe backstop) and `gmail_scan_state` (per-user Gmail after: date
   cursor). RLS: both new tables in `rls_sql.USER_TABLES`.
   `set_outcome` is the single writer; send-side `status` proven
   untouched by test.
2. **Manual markers**: `POST /api/v1/outcomes/{app_id}/outcome`
   (ratelimit `outcome_update`) + Sent-card "What happened?" select.
   Manual outcomes outrank detected ones — `decide_proposal` refuses to
   overwrite a manual outcome and marks the proposal superseded.
3. **Reply detection** (`POST /api/v1/outcomes/scan`, ratelimit
   `outcome_scan`; daily via `run_outcome_maintenance` hooked into the
   scheduled hunt): Composio adapter `gmail_scan.py` discovers tool
   slugs at runtime (vendor rule — never hardcoded), searches by
   sender terms (target email, its domain, posting host, ATS domains),
   reads FULL bodies only (no body → surfaced-uncertain, never a
   snippet). Classification is rule-based EN+SV (no AI spend, no
   nondeterminism): acknowledgments classify to None BY ABSENCE of
   class evidence — an ack pre-check was removed after the red test
   caught it swallowing the most common rejection letter ("Thank you
   for applying. We have decided to move forward…"). Propose-then-
   confirm: scans only file proposals; approval is the sole write path.
4. **Nudges + stats**: `GET /outcomes/nudges` (deadline urgency 7d/🔥 /
   passed/⚠ on the Review tab — the Finish-applying cliff; follow-up
   offer 10d-quiet <2 follow-ups → `create_follow_up_draft` seeded from
   the original package through the SAME review gates, capped at 2
   counting drafts-in-review; confirm-applied prompt for 3d+-stale
   manual hand-offs; 60d quiet sweep proposing `no_response`).
   `GET /outcomes/stats`: honest denominator = sent by us OR
   user-confirmed applied (beta reality: email apply is off, so
   manual hand-offs count only when the user confirms —
   `confirm_applied` nudges exist to make that cheap).

**Deviations from the design sketch, all recorded in code comments:**

- `outcome_source` gained `sweep` (the WO sketch listed manual |
  reply_detection; the 60d sweep files proposals from a third source
  and the column must say where an outcome came from).
- Proposals carry `evidence_received_at`; `resolved_at` is set from it
  on approval so time-to-first-response measures the REPLY, not the
  scan. Consequence: `resolved_at` is set on every outcome write (an
  open `interview` has a first-response time), not only finals —
  final-only semantics would null the stat for the cohort that matters
  most.
- Classification is deterministic rules, not an AI call: zero marginal
  cost, testable offline, and uncertainty surfaces instead of guessing.
  An AI judge can layer on later without touching the contract.

**Not verified live (the one honest open item):** the Composio Gmail
read path (`gmail_fetch_messages`) follows the same
slug-discovery-at-runtime discipline as the WO-07 connection layer and
is unit-tested only through the injected `FetchMessages` contract. The
first real scan (Settings → connected Gmail → Sent page → scan, or the
first scheduled hunt for a connected user) is the live verification —
same first-call discipline as `scripts/composio_first_call.py`. All
failures degrade into `gmail_scan_state.last_error`; they never fail
the hunt.

**Review round 1 (2026-10-15, PR #127 — merged 71a6c97):** external
review found 10 defects in the first cut; all fixed with red-first
tests (19 new). The five that could corrupt outcomes or lose mail:
the 60d sweep could overwrite an interview (now skips employer
signals; no_response over a signal is superseded-not-written;
dismissed sweep proposals never re-file); ATS/aggregator senders
matched every application and attached first-match-wins (now ambiguous
= uncertain unless company/title discriminates uniquely; aggregator
hosts never match); a unique-index collision rolled back the whole
scan's uncommitted proposals while the cursor advanced (now a
SAVEPOINT); follow-up rows inflated stats/sweep/nudges (now excluded
everywhere — a reply updates the parent); time-to-first-response
measured the latest outcome (now the first: signals never move
resolved_at forward, min for earlier evidence). Plus: SV template
actually used (column is `country`, not `country_code`),
compute_nudges scoped + batched, truncated scans hold the cursor at
the last processed message (no mail skipped past the 50-cap), arrival
times normalized to naive UTC, /scan runs off the event loop.
Suites after: 646 (SQLite) / 658 (Postgres) green.
