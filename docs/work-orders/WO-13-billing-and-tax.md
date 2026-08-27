# WO-13 — Billing and tax posture

> Priority: P1 · Blocks: first paying user · Status: decided, not started
> Decision: **Paddle as Merchant of Record.** Rationale and numbers below.
> All external facts verified at source 2026-08-27; re-verify before signing.

## The problem this solves

The product's scope is Sweden + UK. That is two VAT regimes, and the UK one
starts immediately:

- **UK: zero registration threshold for non-established businesses.** A Swedish
  company selling digital services to UK consumers must register with HMRC from
  the **first sale**. No grace period, no small-business exemption — a single
  £19 subscription triggers it, with quarterly filings thereafter.
- **EU: destination-country VAT via OSS.** Below €10,000/year of cross-border
  B2C sales you charge home-country (Swedish, 25%) VAT; above it, destination
  rates of 17–27% reported through a single quarterly OSS return. At €19/month,
  50 users crosses the threshold inside year one.

This is **not** double taxation — place of supply for B2C digital services is
where the customer is, so each sale is taxed once, in the customer's country.
Swedish corporate income tax on profit is separate and unaffected. The cost is
administrative, not fiscal: two foreign registrations and two filing cadences
before there is meaningful revenue.

## The decision

**Use a Merchant of Record.** Paddle becomes the legal seller: we sell to
Paddle, Paddle sells to the consumer, and Paddle carries VAT registration,
collection, remittance and audit liability globally. We register nowhere.

This also removes market-sequencing as a question — SE and UK can open on the
same day, which is what the two-country positioning requires.

### Rejected alternatives

| option | why not |
|---|---|
| **Stripe + Stripe Tax** | Stripe Tax is **not** a Merchant of Record (verified in Stripe's own docs). It calculates, monitors thresholds and *helps manage* registrations — the liability, registrations and filings remain ours. Cheaper per transaction; leaves the actual problem intact |
| **Chargebee** | Not an MoR either — a billing layer requiring a separate gateway underneath. Entry plan carries a **$99/month minimum**, which alone exceeds our entire fixed infrastructure base, and we would still pay Stripe *and* an accountant. Built for billing complexity (usage metering, multi-entity, CPQ, ERP) we do not have |
| **Launch SE-first, defer UK** | A real option before the MoR decision; moot after it. Deferring costs us the UK market to avoid paperwork the MoR removes anyway |

## What it costs

Verified from Paddle's pricing page: **no monthly fee, no minimum, no setup
fee.** Purely per-transaction at **5% + 50¢**, renewals included.

The headline rate is not the effective rate at our price point:

| on a €19 subscription | |
|---|---|
| 5% | €0.95 |
| + 50¢ fixed | ~€0.46 |
| subtotal | **€1.41 — 7.4%** |
| + FX margin (up to 1.5%, if settlement currency ≠ customer currency) | ~€0.29 |
| **realistic effective** | **~€1.70 — ~9%** |

**The fixed 50¢ is the problem, not the 5%** — it is 2.4% on its own at €19.
Flat fees punish low-ticket subscriptions.

Secondary-source items to confirm before signing (note: the most detailed
"hidden fees" write-ups are published by **Dodo Payments, a Paddle
competitor** — the facts are probably right, the framing is not neutral):

- FX margin up to 1.5% on non-settlement currencies — **will apply routinely**
  (Swedish entity, EUR and GBP customers)
- Chargebacks €15–20 each, deducted from balance
- Refunds do not return Paddle's fee
- Paddle Retain (failed-payment recovery) at 10–15% of recovered revenue

## Margin model

At 50 users, €19/month VAT-inclusive:

| line | |
|---|---|
| gross revenue | €950 |
| less VAT (~22% blended) | −€190 |
| **net revenue** | **€760** |
| Paddle (50 × ~€1.70) | −€85 |
| AI inference (50 × ~$4.51) | −€205 |
| fixed infrastructure | −€59 |
| **margin** | **~54%** |

Falling to ~50% is still a viable business; **break-even is ~6–7 paying users**
against the fixed base. After WO-08 removes `cover_note` (−20% on scoring) the
margin recovers roughly 3 points.

For comparison, the Stripe path costs ~€27/month in fees at this scale but adds
an accountant handling two VAT regimes — which will exceed the €58/month
difference. **The MoR is cheaper in cash at our scale, before counting our own
hours.**

**Revisit trigger:** ~500–800 users, where 5% starts to exceed
(gateway + billing platform + compliance). Re-run the comparison then against
Chargebee **+ Stripe + accountant**, not against Paddle alone.

## Design decisions to make once, deliberately

1. **Price display: VAT-inclusive.** EU consumer law requires consumers see the
   total price. €19 means €19 paid. Net therefore varies by country —
   €15.20 from a Swedish customer, €15.83 from a UK one (UK's 20% is *better*
   for us than Sweden's 25%).
2. **Flat €19 gross across markets**, rather than per-market pricing. Simpler,
   and the net variance is small enough not to matter at this scale.
3. **Billing period — decide against the product's actual shape.** The fixed
   50¢ is charged per transaction, so twelve monthly payments pay it twelve
   times (~€20.40/user/year) against an annual plan's single charge
   (~€10.00/user/year).

   But **this product succeeds when the customer stops needing it.** Selling a
   12-month plan to someone hoping to be employed in three is a poor promise
   and an avoidable refund queue. **A 3- or 6-month plan is the honest middle:**
   fewer fixed fees than monthly, and it matches how long a job hunt runs.
   Decide this deliberately — it is a product-ethics call, not a pricing one.
4. **Trial gating** — see WO-05 dependency below.

## Implementation

- **Entitlement check at the route boundary**, as a `require_plan` dependency
  alongside `get_authenticated_user`. Same Layer-1 pattern as auth: services
  must not resolve entitlement themselves, so the unguarded call is not
  expressible.
- **Map plans onto the existing rate-limit buckets** (`hunt`, `match_run`,
  `draft_prepare`) rather than inventing a second quota system.
- **Webhook handling** for subscription lifecycle: created, renewed, payment
  failed, cancelled, refunded. Failed payment must degrade gracefully — never
  delete data, never silently stop hunting without telling the user.
- **Trial:** v1 = count cap (10 jobs *scored* per day — cap scoring, not
  display; money is spent before anything renders). v2 = AI-spend budget once
  WO-05's per-call cost table exists. Do not block v1 on v2.

## Acceptance criteria

- [ ] No route that spends AI money is reachable without an active entitlement,
      proven by a test that calls it with a lapsed subscription
- [ ] **Two-tenant trap:** user A's active subscription never grants user B
      entitlement — asserted at the route boundary, not just in the model
- [ ] Webhook replay and out-of-order delivery handled idempotently (a
      duplicated `renewed` event must not double-extend)
- [ ] Payment failure degrades to read-only access, retains all user data, and
      notifies the user — no silent stoppage
- [ ] Trial cap enforced on **jobs scored**, verified by counting AI calls in a
      test, not by counting rendered cards
- [ ] Price displayed VAT-inclusive; the displayed figure is what is charged

## Open items — confirm with Paddle before signing

1. **Is the 5% charged on the gross (VAT-inclusive) €19 or the net €15.20?**
   Their pricing page does not say. ~€0.19/user/month.
2. **Settlement currency options and the exact FX margin** for a Swedish entity
   receiving EUR and GBP.
3. **Is a lower rate negotiable?** 5% + 50¢ is the public rate; MoRs routinely
   negotiate and asking costs nothing.
4. **Quote at least one other MoR** before committing — they cluster around the
   same 5% band but terms differ. Paddle is simply the one verified at source
   here.

## Consequence for the privacy documentation

Paddle becomes the **legal seller of record**, which places them in the
sub-processor / trust chain alongside Supabase, Resend, Sentry and the model
provider. They handle customer payment data and appear on the customer's
invoice. The privacy policy and sub-processor list must name them — this is not
optional once they are the counterparty to the customer's contract.

## Gate

**One hour with a tax/privacy professional before the first paying user.** It
should cover, in one session: the MoR-vs-direct decision against the Swedish
corporate structure, the Chapter V transfer stack (see MIGRATION.md WO5), and
the AI Act Article 50 synthetic-text marking question. Cheap insurance on
decisions with 4%-of-turnover exposure — and a cheaper hour for walking in with
all three already framed.
