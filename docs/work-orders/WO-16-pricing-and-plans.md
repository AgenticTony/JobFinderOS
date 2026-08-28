# WO-16 — Pricing and plan design

> Priority: P1 · Depends on: WO-13 (Paddle MoR), WO-14 (trial gating)
> Status: decided 2026-08-28 · Supersedes the €19 figure used throughout WO-13
> Competitive research verified at source; re-verify prices before launch.

## The decision

| plan | price | notes |
|---|---|---|
| **Monthly** | **€24.99 / month** | cancel anytime |
| **Quarterly** | **€59.97 once** (€19.99/mo — save 20%) | unused whole months refunded on hire |
| **Trial** | **7 days, no card, no auto-renew** | scoring capped at 10 jobs/day |
| **Annual** | **deliberately not offered** | see below |

All prices VAT-inclusive (EU consumer law requires the total price be
displayed). One product, one feature set, nothing metered.

## Why this shape

### The category's weak point is billing, not features

Verified from public reviews, 2026-08-28:

| product | rating | the complaint |
|---|---|---|
| AIApply | Trustpilot integrity warning; **F rating with the BBB** | $29/mo excludes auto-apply; credits at $10/10 and $39/100 take the real cost to ~$68. "By far the most common complaint" |
| LazyApply | **2.1**, 56% one-star | a 30-day guarantee coexisting with ignored refunds |
| Sonara | 3.9 | a $2.95 trial that silently auto-renews to $23.95 every four weeks |
| Jobright | — | one-star reviews dominated by cancellation friction |
| JobCopilot | 3.8 | the category leader, and that is a B− |

Three of them hide prices behind a login. **Nobody in this category has
clean billing.** Every promise in the section below is drawn directly
from a competitor's one-star reviews, and each one is free to keep.

### Why not tiers

Three price points is the conventional shape, but it does not apply here.
Tiers work when they differ by FEATURE; this product has one feature set,
so the only thing available to meter is job scoring — which IS the credit
model generating AIApply's complaints. Tiering is structurally unavailable,
and that is a good outcome, not a limitation.

Monthly and quarterly are therefore not two tiers. They are one price with
two payment rhythms.

### Why the quarterly needs BOTH a discount and the guarantee

An earlier draft proposed one price with the guarantee as the only
incentive. That was wrong: if unused months are refunded, quarterly is
strictly WORSE for the user — identical effective price, cash gone
upfront, a refund to chase. There is no reason to take it.

The two instruments answer different objections:
- the **discount** answers *"why would I?"*
- the **guarantee** answers *"what if it goes wrong?"*

### Why 20% and not 30%

The discount pays for itself whenever an average monthly subscriber churns
before the break-even point:

| quarterly price | contribution | break-even churn |
|---|---|---|
| €19.99/mo (−20%) | €11.18/mo | **2.3 months** |
| €17.49/mo (−30%) | €9.34/mo | 1.9 months |

20% wins across a wider range of plausible churn. And discounts are easy
to widen and hard to narrow — going to 25–30% later is a clean move;
pulling back from 30% reads as a price rise.

### Why no annual

Not because it would not sell — because it is the wrong promise. Selling
twelve months to someone hoping to be employed in three is exactly what
this product exists not to do. Quarterly renews; someone still hunting at
month four re-ups without ever having been sold a year.

## The numbers

At verified costs (AI ~€3.36/user/month after WO-08; Paddle 5% + 50¢ plus
an assumed 1.5% FX; Swedish VAT 25%):

| | monthly €24.99 | quarterly €59.97 |
|---|---|---|
| net of VAT | €19.99 | €15.99/mo |
| Paddle | €2.08 | €1.45/mo |
| AI | €3.36 | €3.36 |
| **contribution** | **€14.55/mo** | **€11.18/mo** |

**The guarantee is nearly free.** A refund after month 2 costs €0.73/month,
not €19.99 — the refunded month is never served, so there is no AI cost
behind the revenue given back:

```
quarterly, full 3 months       €11.18/mo
quarterly, refunded at month 2 €10.45/mo
```

That is the reason to make the guarantee generous rather than policed.

## Billing promises — these are the product

Each is a competitor's worst review, inverted. They belong on the public
pricing page, in this language:

> No credits. No surprise charges. The trial doesn't auto-renew.
> Cancel in one click. And if you get hired, we refund every month
> you haven't used.

1. **No credits, ever.** The subscription includes everything. The trial's
   scoring cap is a trial limit and must never become a paid-tier limit.
2. **The trial does not auto-renew.** It ends; then the user chooses.
3. **Refunds honoured on request, without proof.** No offer letter, no
   documentation. Asking a user to email an offer letter would collect
   employer identity and salary data we have no basis to hold — a GDPR
   liability on the product whose differentiator is careful data handling —
   and would convert a gift into a hurdle. Ask "where did you land?"
   optionally, and only for the people who want to tell you.
4. **Cancel in one click, in-app.**
5. **Price visible on the public page**, never behind a login.

## Acceptance criteria

- [ ] Prices displayed VAT-inclusive; the displayed figure is what is charged
- [ ] Trial expires without charging; a test asserts no subscription is
      created at trial end absent an explicit upgrade
- [ ] Cancellation is reachable in one action from the account page and
      takes effect without support contact
- [ ] Refund of unused whole months is issued on request with no
      documentation required; the flow is tested end to end
- [ ] No code path meters, deducts or limits job scoring for a PAYING user
      — asserted by a test, because this is the promise most likely to
      erode under future cost pressure
- [ ] The five promises appear verbatim on the pricing page

## Open items

1. **Confirm with Paddle** whether the 5% applies to the gross
   (VAT-inclusive) €24.99 or the net €19.99, and the exact FX margin for a
   Swedish entity settling EUR and GBP. Together they move contribution by
   roughly €0.40 (WO-13's pre-signature list).
2. **Do not add a discount tier before there is conversion data.** If
   quarterly conversion disappoints after ~50 signups, test 25%; measure,
   do not guess.
3. **UK pricing** — £ display for UK customers, or flat € everywhere. UK
   VAT at 20% nets slightly better than Sweden's 25%; the MoR removes the
   registration question either way (WO-13).
