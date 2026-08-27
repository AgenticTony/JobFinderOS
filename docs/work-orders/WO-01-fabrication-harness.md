# WO-01 — Fabrication harness

> Priority: P0 · Blocks: first real user other than the owner
> Invariant enforced: #4 (zero fabrication in tailoring)
> Status: **executed 2026-08-27** — all three layers built, tested
> red-first, revert-checked, and MEASURED LIVE. Headline findings from
> the live judge run (5 real approved jobs, the owner's CV):
>
> - **The tailoring prompt has a real fabrication problem — this is
>   WO-02's evidence base.** The judge (fresh-call, no tailoring
>   context) found unsupported claims in 4/5 documents: invented work
>   authority ("EU citizen, full work rights"), competence inflation
>   ("masters both frontend..."), invented tool familiarity (LangChain,
>   Azure OpenAI, Lovable), unsupported competences. Snapshots saved as
>   permanent fixtures: tests/fixtures/fabrication/live_catch_*.json.
> - **Layer A's deterministic checker converged to zero
>   high-confidence FALSE positives on real output** after three
>   triage rounds against the live FP classes (addressee companies,
>   glued skill runs across connector languages, country enumerations,
>   phrase-glue — resolved by the corporate-marker/3+token tier rule:
>   markerless 2-token org runs are advisory, never regenerated).
> - Fabrication rate is now a measured number with a denominator, per
>   the WO's final criterion.

## Why this exists

`tailor_application` generates a CV and cover letter that go to a real
employer, under the user's real name, describing the user's real career.
Invariant #4 says every fact must trace to the original CV.

Today that invariant is enforced by **prompt text alone**:

```
- ZERO FABRICATION: every employer, date, skill, title, achievement and metric in
  the tailored CV and cover letter must be traceable to the original CV. Never
  invent, upgrade, or embellish anything.
```

There is no test, no runtime check, and no measurement. Every test that
touches tailoring mocks `tailor_application`
(`tests/test_units.py:846`, `tests/test_multiuser.py:521`) — they verify
*whose* profile was used, never *what was written*.

A model that invents "AWS Certified Solutions Architect" or moves an
employment date to close a gap produces a document the user sends in good
faith and is caught out by at interview. This is the worst outcome the
product can produce: worse than missing a job, worse than downtime, and in
regulated sectors it is fraud. It is currently the least-guarded path in
the codebase.

## The hard part (read before designing)

Naive string matching does not work, for one specific reason:

**The tailored document is not necessarily in the CV's language.** The
tailoring prompt says: *"Write both documents in the language of the job
posting (a German posting gets German documents)."* An English CV applying
to a Swedish posting produces a Swedish CV. A naive "does this sentence
appear in the source" check flags the entire document as fabricated.

The resolution: **check only translation-invariant atoms deterministically,
and send everything semantic to a judge.** Employers, dates, numbers,
certifications and technologies survive translation ("Svenska Spel" stays
"Svenska Spel"; `Python` stays `Python`; `2019–2022` stays). Job titles and
prose do not ("Developer" → "Utvecklare") and must never be strict-matched.

Second trap: Swedish text. Do **not** normalise by stripping diacritics —
`å ä ö` are distinct letters, not accented `a`/`o`. Casefold and collapse
whitespace only.

## Deliverable

### Layer A — deterministic checker (always-on, zero API cost)

`backend/app/services/fabrication.py`

```python
def unsupported_claims(source_cv: str, tailored_text: str) -> list[Claim]:
    """Atoms asserted in tailored_text that do not appear in source_cv.

    Checks ONLY translation-invariant atoms. Semantic fidelity is Layer B's
    job — a claim absent here is not proof of honesty, only of the absence
    of a mechanically detectable invention.
    """
```

`Claim` carries: `kind`, `value`, `context` (the surrounding sentence), so the
UI can show the user *where* the unverified claim appears.

Atom kinds, all translation-invariant:

| kind | extraction | rationale |
|---|---|---|
| `year` | `\b(19\|20)\d{2}\b` and ranges | a shifted date closes an employment gap |
| `organisation` | capitalised token runs, minus a stoplist of section headers | the invented employer |
| `credential` | degree/cert patterns (BSc, MSc, PhD, AZ-\d+, "Certified …") | the invented qualification |
| `metric` | percentages, currency, "N years", team sizes | the inflated achievement |
| `technology` | tokens matched against a vocabulary **extracted from the CV itself**, not a global list | the skill they don't have |

Matching is casefold + whitespace-collapsed substring against the source CV,
with diacritics preserved.

### Layer B — LLM judge (opt-in, mirrors `RUN_CALIBRATION`)

Behind `RUN_FABRICATION=1`, as `tests/test_fabrication.py`. Runs *real*
tailoring on N real approved jobs, then a **separate** judge call: given the
source CV and the tailored document, list every claim about this person that
the CV does not support. Assert zero. Costs API calls; not in default CI.

The judge must be a fresh call with no tailoring context — asking the same
conversation to grade its own output measures agreeableness, not fidelity.

### Layer C — runtime guard (tiered)

The harness is the *test*. The **control** is this layer: a check that runs on
every draft, in production, before anything reaches the review screen. Human
review already exists, but reviewers rubber-stamp — a control that depends on
human vigilance to catch a rare event is not a control. This must make the
process enforce the invariant, not the reader.

Run Layer A at draft creation. Persist findings on `ApplicationDraft` (new
nullable JSON column, Alembic migration).

**Do not apply one uniform action to all findings — the atom classes have very
different false-positive rates, and the correct response follows the rate:**

| tier | classes | FP rate | action |
|---|---|---|---|
| **High confidence** | `credential`, `organisation`, `year`, `metric` | low — these are translation-invariant and near-exact | **Regenerate**, with the offending claim named in a correction instruction. Re-check. Up to 2 retries |
| **Advisory** | `technology` | high — "Azure" vs "Microsoft Azure", "Postgres" vs "PostgreSQL" | **Flag** in the review UI with surrounding context. Never auto-act |

If a high-confidence finding survives 2 regeneration attempts, **block the
send** and tell the user precisely which claim could not be traced to their CV.
Blocking is correct here: at that point it is not a heuristic firing once, it
is the model repeatedly asserting something the CV does not support.

**Regenerate, never strip.** Silently deleting the offending sentence leaves a
mutilated document the user did not write and cannot see was altered — and if
the finding was a false positive, it has quietly removed a *true* fact from
their CV. Regeneration keeps the document coherent and keeps the user's real
history intact.

Every regeneration and every block is recorded, so PRD.md's "fabrication rate:
0" becomes a measured number with a denominator rather than an aspiration.

## Proving it works

Per standard #2 — the test must be seen red, and the proof must be
deterministic so it holds in CI without API spend.

`backend/tests/fixtures/fabrication/` — two fixtures built from the same
source CV:

- `clean.json` — a faithful tailored output. Asserts **zero** findings.
  This is the false-positive guard, and it is the harder of the two: a
  checker that flags everything trivially passes the fabricated case.
- `fabricated.json` — the same document with five planted defects:
  1. an employer not in the CV (`Acme Global Ltd`)
  2. a certification not in the CV (`AWS Certified Solutions Architect`)
  3. a shifted employment year (CV says `2019`, output says `2017`)
  4. an inflated metric (CV says `12%`, output says `40%`)
  5. a technology not in the CV (`Kubernetes`)

The test asserts each planted string is named in the findings — not merely
that the count is ≥5. A checker that returns five unrelated false positives
must fail.

Add a third fixture once Layer B has run against real output: any genuine
fabrication it catches becomes a permanent regression fixture.

## Acceptance criteria

- [ ] `unsupported_claims` exists and is imported by tests from the **production
      module**, never a copy in the test file (a shadow copy is how
      `rescore_backlog.py` diverged from the matcher three times)
- [ ] `clean.json` → zero findings; `fabricated.json` → all five planted
      strings named
- [ ] Layer A runs in default CI, costs no API calls, adds < 1s
- [ ] `RUN_FABRICATION=1` runs Layer B against real tailoring output
- [ ] Draft creation persists findings; high-confidence findings trigger
      regeneration; advisory findings render in the review UI with context
- [ ] A high-confidence finding surviving 2 retries blocks the send and names
      the untraceable claim
- [ ] Regeneration and block counts recorded for the fabrication-rate metric
- [ ] Swedish-language round trip verified: an English CV tailored to a
      Swedish posting produces **zero** `organisation`/`technology` false
      positives on the fixture
- [ ] Fabrication rate recorded, so PRD.md's "0" target is measured rather
      than asserted

## Out of scope

Blocking sends. Rewriting the tailoring prompt (if Layer B shows a real
fabrication rate, that becomes WO-02's problem, and the harness is how we
will know the fix worked).
