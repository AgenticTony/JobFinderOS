# WO-23 — Blocked-draft recovery: edit, re-check, vouch

> Priority: P1 · Depends on: WO-01 (fabrication guard) ✅, WO-02 (judge) ✅
> Status: **executed 2026-09-15** (options A + B, owner decision 2026-09-11)
> **Touches the fabrication guard — treat as a SAFETY change.**

## Why

A draft the fabrication guard blocks is a dead end. The owner hit it on
2026-09-10 (Experis, *Junior helpdesktekniker*): the guard flagged
"Microsoft Office (daglig användning under 20 års yrkesliv)" and
"Operativsystem, nätverk och kringutrustning (utbildning och projekt)".
The guard was **right on both** — neither is in the CV, and "daglig
användning" was lifted from the CV's *languages* line and grafted onto
Office. But the only way forward was re-uploading the whole CV.

The fix is small because most of the machinery exists. Verified
2026-09-11:

| fact | evidence |
|---|---|
| The blocked text **is persisted** | `draft_service.py:228-229` write `cover_letter`/`tailored_cv` before the guard; the block path (`:294-310`) never clears them |
| The flagged claims are **discarded** | block sets `fabrication_findings = None` (`:295`); they survive only as prose in `draft.error` |
| Edits are **already accepted** on failed drafts | `save_draft_edits` (`:338`) refuses only `submitted` |
| Nothing moves `failed → ready` | submit requires `ready` (`:419`) |
| The UI hides everything | documents/editor panel gated on `status === 'ready'` (`page.tsx:1532`); a failed card shows only `draft.error` (`:1492`) |
| Regenerate **already exists** in the API | `create_draft_for_job` reuses a failed draft and regenerates it — only `ready` short-circuits without `force`. No UI button calls it for failed drafts |
| User edits are **never re-checked** | `save_draft_edits` runs no guard. The trust model already is: the guard polices AI output; what the user writes is theirs |

## The principle

The guard exists to stop **the AI inventing claims on the user's
behalf**. It must never stop **the user saying something true about
themselves**. The public promise moves from *"a claim not in your
history does not ship"* to *"no claim ships that you haven't written or
confirmed"* — still true, still the differentiator.

## Hard constraints

1. **Per-claim resolution, never a whole-draft override.** No "send
   anyway". Each flagged claim is fixed in the text or individually
   confirmed.
2. **The re-check cannot be the only exit.** Re-checking text that
   deliberately keeps a true-but-undocumented claim ("Microsoft Office")
   flags it again, forever. Remaining flags resolve by fix-in-text **or**
   "This is true".
3. **Guard source ≥ generator input** (the 2026-08-31 invariant stated in
   `draft_service.py`). Vouched facts feed **both** the tailoring prompt
   and the guard's evidence. Guard-only → the AI never uses them;
   prompt-only → the AI uses them and the guard blocks it.
4. **`fabrication_blocked` is never cleared on recovery.** It has no
   readers today but it is the raw data for the fabrication rate
   (`models/draft.py:56-62`). Recovery is recorded separately.
5. **One implementation of "check a package".** Extract the Layer A +
   judge sequence out of `create_draft_for_job` into a shared helper;
   generation and re-check call the same function. Duplicated thresholds
   are what dismissed 62 matcher rows on a single sample.
6. **A vouched fact is a fact, not a verdict.** A blanket entry such as
   "everything in my applications is true" must not clear an unrelated
   claim the AI invented — otherwise one line silently disables the guard
   for the AI's own fabrications. Layer A is substring-on-atoms and
   already safe; the judge's evidence block must present vouched facts as
   a delimited list with the instruction *"each line supports only what
   it states"*.

## The flow

A failed draft with `fabrication_blocked = true` **and** documents present:

1. The card opens the **normal editor** (the same textareas as a ready
   draft) plus a **"Why this was blocked"** panel: each flagged claim,
   with the sentence it appears in.
2. The user edits freely (existing `PUT /draft/{id}`).
3. **Check again** → `POST /draft/{id}/recheck` runs the shared check on
   the *current* text, with evidence = CV + profile context + vouched
   facts + this draft's attestations.
   - **Clean** → `status = ready`, `fabrication_resolved_at = now`.
     Normal Review & Send from here.
   - **Still flagged** → stays `failed`; the panel shows only what
     remains, each with **[This is true — keep it]** and a checkbox
     **"Also add to my profile"** (default **on**).
4. **This is true** → `POST /draft/{id}/attest {claim, save_to_profile}`
   records the attestation on the draft and, if checked, appends to
   `profile.vouched_facts`. When no unresolved claim remains the draft
   goes `ready` — no further AI call; the rest of the text passed in
   step 3.

**Every failed draft** also gets **Regenerate without these claims**,
wired to the existing `prepareDraft`.

A failure that is **not** a guard block (timeout, malformed response, no
documents) shows no editor — only **Try again**. Gate the editor on
`fabrication_blocked && hasDocuments`, not on `status === 'failed'`.

## Part B — "Things I can vouch for" (Profile)

- `profiles.vouched_facts` — `Text`, JSON list of strings, nullable, plus
  an Alembic migration.
- Profile UI: a list editor beside **Hard excludes** (`page.tsx`
  ~1663-1675). Copy: *"Skills and facts that are true but aren't on your
  CV. We'll use them, and we won't flag them."*
- `build_profile_context` renders it **outside** the `include_derived`
  gate (user-entered, like location and languages), so it reaches the
  generator (default call) **and** the guard (`include_derived=False`).
- Bound it: 30 items × 200 characters.
- The original CV stays immutable (invariant #1) — this is the addendum,
  not an edit.

## Data

- `application_drafts.fabrication_attested` — `Text`, JSON list of
  `{claim, at, saved_to_profile}`.
- `application_drafts.fabrication_resolved_at` — `DateTime`, nullable.
- The block path persists `findings_as_json(high + advisory)` instead of
  `None`. `findings_as_json` already emits `tier` (`fabrication.py:500`).
- **Sweep:** the ready card's advisory panel (`page.tsx:1498`) renders
  *every* finding under "Verify before sending — tech not found in your
  CV". Once the column holds `tier: high` items that label is wrong —
  filter it to `tier === 'advisory'` and show attested claims separately.

## Copy sweep — same PR

- `frontend/src/i18n/dict.ts:94` (EN) *"If a claim is not in your
  history, it does not ship."* → *"If a claim isn't in your history, it
  doesn't ship unless you confirm it."*
- `dict.ts:282` (SV) — the same change.
- `fabrication_block_error` (`draft_service.py:83`): *"Edit your CV to
  include them, or regenerate."* → *"Fix or confirm them below, or
  regenerate."*
- **Fix the evidence quoting in the same area** (found 2026-09-10): the
  panel this WO builds becomes the primary UI for blocks, so its evidence
  must be right. `_CLAIM_STOPWORDS` (`draft_service.py:44`) lacks Swedish
  function words — both quoted "Your CV says" lines matched only the
  preposition *under* — and `_cv_lines_near_claims` (`:72`) takes the
  first two matches top-down instead of ranking by overlap, which crowded
  out the line that mattered (*Svenska (god nivå, daglig användning)*).

## Rate limits

- `draft_recheck`: 10/hour — the judge is a paid LLM call.
- `draft_attest`: 60/hour.

## Acceptance

- A blocked draft shows its documents in the editor and lists every
  flagged claim.
- Edit out the claims → **Check again** → `ready` → submit works.
- Keep a claim → **This is true** → `ready`; the claim is in the **sent**
  artifact; `fabrication_blocked` is still `true`;
  `fabrication_attested` records it.
- "Also add to my profile" → a later draft for a different job using the
  same claim is not flagged.
- Any unresolved flagged claim → submit still returns 400.
- A non-guard failure shows only **Try again**.
- EN and SV copy updated.

## Tests

In `tests/test_multiuser.py`, beside
`test_surviving_fabrication_blocks_and_names_the_claim` (`:1772`) and
`test_judge_finding_regenerates_then_blocks` (`:1967`). **Assert on the
outbound artifact, and red-prove each one** (revert the fix, watch it
fail, restore) — per CLAUDE.md standard 2.

1. The block persists structured `tier: high` findings, not `None`.
2. Re-check on edited clean text → `ready`; `resolved_at` set;
   `fabrication_blocked` still `true`.
3. Re-check with the claim kept → still `failed`, claim listed; attest →
   `ready`.
4. An attested claim reaches the email payload **and** the PDF text.
5. An unresolved claim → submit 400.
6. **Composition:** a vouched fact reaches the tailoring prompt **and**
   the guard's source — assert both on one run (the producer → consumer
   boundary that shipped broken twice in the taxonomy work).
7. A blanket vouched fact does not clear an unrelated invented claim.
8. Tenancy: re-check / attest on another user's draft → 404; attest
   never writes another user's profile.
9. A recovered `ready` draft's advisory panel shows only `tier:
   advisory`.
10. Both new rate-limit buckets are enforced.

## Out of scope

- Re-checking edits on drafts that never failed — today's trust model
  stands (owner decision 2026-09-11: the re-check is for drafts that
  failed first time round).
- Editing the original CV — immutable by invariant #1.

## Execution record (2026-09-15)

All acceptance criteria verified; 14 red-first tests in
`TestWO23BlockedDraftRecovery` + 2 in `TestFabricationBlockMessage`
(evidence-quoting fix), revert-checked per CLAUDE.md standard 2.
Full suite 488 green / 14 skipped; ruff, tsc, `next build` clean.

Landed as designed, plus what implementation surfaced:

- **`check_package`** (draft_service) is the one implementation of
  "check a package": Layer A + judge over given text, guard source =
  CV + user-entered context + this draft's attestations. Generation
  and re-check call it; the block path persists
  `findings_as_json(high + advisory)` instead of discarding them.
- **Attestation matching is containment, not equality** (`_claims_match`):
  Layer A extracts up to three variant findings from one credential
  sentence ("AWS Certified Solutions Architect" also yields "aws
  certified" + "certified solutions architect") — a human confirms the
  claim ONCE; the confirmation covers its variants. The UI mirrors the
  same rule client-side.
- **`confirmed_facts_block`** (cv_service) is the single rendering of
  user-confirmed facts — the header sentence ("each line supports only
  what it states") is the constraint-6 guard against blanket vouches.
  `build_profile_context` renders vouched facts through it OUTSIDE the
  include_derived gate; the guard source appends attestations through
  the same function.
- **`TAILOR_INPUT_COMPOSITION_VERSION` 1 → 2** (AI-13 discipline): the
  vouched-facts block changes what the model can see; compositions stay
  distinguishable for WO-02 measurement. Pin now `t2-2f8f4e86`.
- **Evidence-quoting fix**: Swedish function words added to
  `_CLAIM_STOPWORDS` (the Experis "under"-only matches);
  `_cv_lines_near_claims` ranks by shared-token count (stable by CV
  order) instead of first-two-top-down.
- **Bounds**: strict 30×200 at the profile editor (400 back to the
  user); LENIENT at attest — the attestation always lands, the profile
  save is skipped past the bound (a draft must never stay blocked over
  a list bound).
- **Lifecycle**: vouched facts die with the profile row and attestations
  with the draft row in the GDPR cascade; both new rate-limit buckets
  are user-keyed so `clear_user` purges them with no code.
- The outbound-artifact test asserts the attested claim in the email
  body AND the exact text the employer-facing PDF was rendered from
  (renderer-input spy — fpdf2's Unicode streams are not byte-greppable,
  and that fragility belongs in no test).

## Review round (2026-09-15, fix.md — 7 findings, all fixed)

The recovery paths as first built gave ways AROUND the guard. All seven
findings verified real and fixed; each fix's test was seen red before
it (the full pre-fix red run + flip-based red-proofs for R1/R2 — never
`git checkout`, per lesson #7):

- **R1 (must-fix)**: attest-ready shipped text the guard never fully
  checked — a Layer-A-blocked draft was never judged (the judge only
  runs on a Layer-A-clean document), and edits after the block were
  never re-checked. Fix: confirming the LAST unresolved claim re-runs
  `check_package` on the current text (attested claims ride in the
  source); ready only if clean, new findings surface for the same
  resolve loop. This supersedes the WO's "no further AI call" line —
  the final check costs one Layer A (+judge when enabled) call.
- **R2 (must-fix)**: `_claims_match` containment let one API call
  (`claim="e"`, or the whole pasted letter) resolve every finding — a
  whole-draft override, exactly what the WO forbids. Fix: acceptance is
  EXACT casefolded match to a flagged value (≤200 chars); variants
  resolve only within the SAME extraction sentence
  (`_resolves_finding`, attested entries record the finding's context).
  The frontend `covers()` mirrors the same rule.
- **R3 (must-fix)**: recheck/attest gated only on `fabrication_blocked`
  (never cleared) — a SUBMITTED draft could be rechecked back to
  'ready' and emailed twice. Fix: both require `status == 'failed'`;
  a replayed attest click still no-ops idempotently before the gate.
- **R4**: vouched facts reached the MATCH-scoring prompt via
  `build_profile_context` default — score comparability under one
  `MATCHING_INPUT_COMPOSITION_VERSION` broken, one job's attestation
  shifting unrelated jobs' scores. Fix: `include_vouched=False` at the
  matcher call site; vouched facts serve tailoring + guard only.
- **R5**: save-to-profile stored the bare flagged atom — a vouched
  "40%" blessed every future 40% claim via substring. Fix: the profile
  stores the finding's CONTEXT SENTENCE (the human-meaningful unit);
  judge-kind findings store the claim (their context is the "why").
  Per-draft attestations stay value-based — they die with this text
  (R6) and only scope THIS draft.
- **R6**: the regeneration ready-path still reset
  `fabrication_blocked = False` — constraint 4 violated, the block
  vanished from the fabrication-rate data. Fix: never written on any
  recovery path; regeneration also clears the replaced text's
  attestations (their durable channel is profile.vouched_facts).
- **R7**: the Article 20 export omitted `vouched_facts` and
  `fabrication_attested`. Fix: both in the export payload (raw JSON
  strings, same convention as `languages`/`search_queries`), tested
  beside TestGDPRExportCompleteness.

Suite: 495 passed / 14 skipped; ruff, tsc, `next build` clean.

## Review round 2 (2026-09-15 — 3 findings, all fixed)

R5's sentence save over-corrected: it vouched MORE than the user
confirmed. All three findings verified real and fixed red-first:

- **N1 (safety)**: confirming one flagged atom saved its whole context
  sentence to `vouched_facts` — every other claim in that AI-written
  sentence ("team of 12" beside a confirmed "40%") became permanent
  guard truth, the R1 final check inherited it as user-confirmed, and
  the panel's 120-char truncation meant the user could vouch text they
  never saw. Fix: the profile save takes ONLY the explicit
  `profile_fact` the client sends (the UI shows the FULL sentence and
  "Will add to profile: …" before the opt-in; checkbox disabled with a
  note past 200 chars); the backend derives nothing — `save_to_profile`
  alone saves nothing. Tested both ways: no explicit fact → vouched
  stays empty and the judge can still flag the sentence's other claims
  on the same draft; explicit fact → exactly that text saves.
- **N2**: attest accepted facts to 300 chars while the profile editor
  rejects >200 — one long sentence blocked every later preferences
  save (the form resends the whole list). Fix: ONE bound
  (`VOUCHED_FACTS_MAX_CHARS`) at both write sites; round-trip test
  (attest → PUT /profile/me → 200).
- **N3**: R2's blanket 200-char claim cap rejected judge findings,
  whose values are free text — "This is true — keep it" 400'd on
  exactly what the guard listed (the original dead end, back for long
  claims). Fix: cap removed; EXACT match to a stored flagged value is
  the override gate (the "e"/pasted-letter attacks needed containment,
  which is gone). Tested with a 230-char judge claim → confirm → ready.

Suite: 498 passed / 14 skipped; ruff, tsc, `next build` clean.

## Review round 3 (2026-09-15 — 1 finding, fixed)

**N1 residual**: the round-2 backend gate was right, but the UI still
sent the finding's full sentence as `profile_fact` by default — a plain
"This is true — keep it" click on "40%" posted "Led a team of 12 … 40%"
and the backend saved it before the final check, exactly what N1 was
meant to stop. The round-2 tests only covered the raw-API path without
a fact, missing the payload the product actually sends.

Fix (all three of the reviewer's asks):

- The per-claim checkbox now saves ONLY the claim itself — enforced in
  the BACKEND: `profile_fact` is saved iff it casefold-equals the
  confirmed claim. A sentence-shaped fact (the old UI's payload) saves
  nothing, whatever a client sends.
- Saving the full sentence is its own explicit action: a separate link
  under the claim opens a confirm dialog showing the whole sentence,
  then saves through the profile-editor channel (`PUT /profile/me`) —
  the user-entered-facts surface, never the attest opt-in.
- The frontend-shaped test (`test_sentence_shaped_fact_is_not_saved_
  by_attest`) posts `profile_fact = context` exactly as the old UI did
  and asserts: nothing lands in `vouched_facts`, the confirmed-facts
  block in the FINAL CHECK's captured judge source lacks the sentence,
  and the judge can still flag "team of 12". Red-proven by flipping the
  equality gate out.

Checkbox label now shows exactly what it saves ("Also add '40%' to my
profile"); the sentence link only appears when the sentence differs
from the claim and fits the 200-char bound.

Suite: 500 passed / 14 skipped; ruff, tsc, `next build` clean.
