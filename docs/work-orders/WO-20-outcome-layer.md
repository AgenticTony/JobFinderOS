# WO-20 — Outcome layer: canonical statuses, reply detection, deadline & follow-up nudges

> Priority: P1 · Depends on: none hard (reply detection rides the
> Composio Gmail connection, proven in production 2026-08-31) ·
> Status: designed, not started
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
