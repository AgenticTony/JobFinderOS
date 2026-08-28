# WO-17 — Cancellation feedback loop

> Priority: P2 · Depends on: WO-13 (Paddle MoR), WO-16 (pricing)
> Status: not started

## Why

Cancellation is the highest-signal moment a subscription product has. The
user has decided, so they have no reason to be polite — and the answer
splits cleanly:

- **They got hired.** The product worked. They are the only people who
  will ever give an unprompted 5-star review, and the category's ratings
  (JobCopilot 3.8, Sonara 3.9, LazyApply 2.1) show how low that bar is.
- **They did not.** This is the only honest diagnostic the product will
  ever get, and it arrives from someone with nothing left to lose.

## The constraint that shapes everything

**The survey must never stand between the user and cancelling.**

WO-16 promises "cancel in one click", and the competitive research is
explicit about why: Jobright's one-star reviews are *dominated by
cancellation friction*. A survey placed before the cancel button IS that
friction, and building it would forfeit a differentiator to collect data.

**Cancel first. Ask after.** The subscription is ended and confirmed
before a single question appears, and every question is skippable.

## Second constraint: the MoR owns the billing UI

Paddle is the merchant of record, so a user who cancels through Paddle's
own customer portal never touches our UI and we never see the moment.

Therefore:

1. **Cancellation must be initiated in-app**, with our backend calling
   Paddle's cancel API — required by the one-click promise anyway, and it
   is the only way we own the survey moment.
2. **A `subscription.canceled` webhook fallback** catches cancellations
   that happen in Paddle regardless (support-initiated, card expiry,
   Paddle's portal). Those get the survey by email instead, once.

## The flow

```
[Cancel subscription]
        ↓  (cancelled immediately, confirmed on screen)
"You're cancelled — you keep access until <date>."
        ↓
"Mind telling us why?"  ← optional, dismissible, one question
        ↓
   ┌────┴─────────────────────────────┐
 "I got a job"                    everything else
   ↓                                   ↓
 Congratulations.                 One follow-up:
 "Want your unused months          "What was missing?"
  refunded? Just say so."          (free text, optional)
   ↓
 THEN, and only then:
 "Would you tell others?"
  → Trustpilot / G2 link
```

**Only the hired branch is ever asked for a public review.** Asking a
disappointed churner to review you publicly is how a 2.1 rating happens.

**The refund is the incentive.** "Did you get hired? We will refund your
unused months" is not a favour they do us — it is how they claim money
they are owed under WO-16. That is why this survey will get answered when
generic churn surveys do not.

## The one question

Keep it to a single choice with routing. Churn surveys die past one
question. Options map onto real work orders, so the data is actionable:

| answer | what it tells us |
|---|---|
| I got a job | success — refund + review ask |
| Not enough relevant jobs | pool coverage (WO-06 routing, WO-15 discovery) |
| The matches weren't good | scoring calibration |
| The CV / cover letters weren't good | tailoring quality (WO-02) |
| Too expensive | pricing (WO-16) |
| Taking a break / other | neutral churn |

Free text is a single optional box on the non-hired branch only.

## Data model

`cancellation_feedback`: `user_id` (FK, nullable), `reason` (enum),
`comment` (text, nullable), `plan`, `months_active`, `created_at`.

**GDPR:** the row is personal data about an identifiable user. It joins
the existing `DELETE /api/v1/account/delete` cascade and the
`/api/v1/account/export` payload — no exceptions for analytics
convenience. If a user deletes their account, their feedback goes with it;
the aggregate reason counts survive because they are counts, not rows.

## Acceptance criteria

- [ ] Cancelling takes one action and completes **before** any question is
      shown — asserted by a test that cancels and checks subscription
      state without answering anything
- [ ] Every question is skippable; dismissing the survey leaves the
      cancellation intact
- [ ] The review request appears **only** on the "I got a job" branch —
      asserted by a test, because this is the rule most likely to be
      loosened later for growth reasons
- [ ] The refund offer is presented on the hired branch without requiring
      proof (WO-16 promise 3)
- [ ] `subscription.canceled` webhook produces exactly one survey email,
      idempotent under Paddle's retries
- [ ] `cancellation_feedback` rows are removed by account deletion and
      included in account export — two-tenant trap: user A's feedback is
      never visible to user B
- [ ] Reason counts are queryable without reading free text

## Out of scope

Retention offers ("stay for 50% off"). They are the other half of what
generates cancellation-friction complaints, and offering a discount to
someone who just got hired is absurd. If retention offers are ever added,
they belong on the *non-hired* branch only, and after the cancellation has
already completed.
