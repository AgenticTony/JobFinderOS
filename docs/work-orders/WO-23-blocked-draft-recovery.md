# WO-23 — Blocked-draft recovery: edit, re-check, vouch

> Priority: P1 · Depends on: WO-01 (fabrication guard) ✅, WO-02 (judge) ✅
> Status: not started · Owner decision 2026-09-11 (options A + B)
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
