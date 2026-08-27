# JobFinderOS — Product Requirements

> Owner: Anthony Foran · Status: re-founded 2026-08-27 · Supersedes: nothing
> (this is the first document that states what the product IS, rather than
> how it is built)

## The problem

Being unemployed is stressful, and the job hunt makes it worse in three
specific ways:

1. **Search is repetitive and low-yield.** The same queries across the same
   boards, most results irrelevant, every day, indefinitely.
2. **Tailoring is the bottleneck.** Everyone knows a tailored CV and cover
   letter beat a generic one. Almost nobody does it for every application,
   because it takes 30–60 minutes each.
3. **Nothing tracks state.** Which applications went out, when, with which
   CV, and what came back — usually a spreadsheet, usually abandoned.

The compounding effect: the seeker spends their scarcest resource (energy,
during the period they have least of it) on the lowest-value part of the
process.

## What JobFinderOS does

One CV on file. The system hunts continuously, scores every relevant job
against that CV, and presents only what clears the bar. The seeker approves
a match; the system writes a CV and cover letter tailored to *that* posting,
grounded strictly in facts from the original CV. The seeker reviews, edits,
and sends. Everything sent is retrievable forever.

**The product is not "apply to more jobs." It is "spend your effort only on
the jobs worth your effort, and never write the same paragraph twice."**

## Who it is for

Primary: an active job seeker in Sweden or the UK with EU/UK work rights,
applying to knowledge-work roles, sending 10–40 applications a month.

Explicitly profession-agnostic. Queries and titles derive from each user's
own CV at onboarding — a nurse gets `undersköterska, vårdcentral`; a
developer gets `utvecklare`. Any design that assumes tech roles is a bug.

Not for: passive career browsers, recruiters (that is TalentHive, the
inverse product), or bulk/spray applying.

## What it must never do

These are product rules, not implementation details. Violating one is a
release blocker, not a bug ticket.

1. **Never send anything without explicit human approval.** Approve the
   match, then review the draft, then send. Two gates, always.
2. **Never state a fact about the seeker that is not in their CV.** The
   tailored CV and cover letter go to real employers under the user's name.
   An invented employer, date, degree, or certification is the single worst
   outcome this product can produce — worse than missing a job, worse than
   downtime. It can end a candidacy and, in regulated sectors, it is fraud.
3. **Never mutate the original CV.** Written once at upload, read-only
   forever. Every tailored version lives in its own row.
4. **Never leak one user's data into another user's outbound artifact.**
   Tested on the artifact — the AI prompt, the email payload, the PDF — not
   just on row ownership.
5. **Never address the seeker as "the candidate."** All output talks TO the
   person ("Your Azure experience…"). They are the user, not the subject.

## Success metrics

Measured, not asserted. Nothing here is currently instrumented — that is a
gap, not an omission.

| Metric | Definition | Target |
|---|---|---|
| **Local relevance** | share of a user's scored pool within their stated commute/remote scope | > 60% |
| **Time to first value** | signup → first reviewed match in the queue | < 24h |
| **Approval rate** | matches approved / matches shown | 15–40% (below = noise; above = bar too low) |
| **Draft acceptance** | drafts sent without heavy edit | > 60% |
| **Fabrication rate** | tailored outputs containing an unsupported claim | **0** |
| **Cost per active user** | measured AI spend / month | < $5 (measured $4.51 at verified prices) |

The first metric is the one currently failing. See ARCHITECTURE.md,
"Known defects".

## Scope boundaries

**In scope now:** Sweden + UK. Email applications. Manual/browser apply for
portals. One CV per user.

**Deferred, with triggers:** US/AU expansion (trigger: SE+UK retention
proven, and the Adzuna/aggregator commercial-terms question answered —
in US/AU the aggregators *are* the backbone, unlike SE/UK). Playwright ATS
drivers for structured portal applies. Multiple CVs per user.

**Out of scope:** interview prep, salary negotiation, recruiter outreach,
any feature that applies on the user's behalf without review.
