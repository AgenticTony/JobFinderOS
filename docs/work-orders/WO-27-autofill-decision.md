# WO-27 — Decision WO: review-gated autofill — build, partner, or defer

> Priority: P3 (a decision, not a build) · Depends on: WO-20 · Status:
> not started
> Rationale owner: `docs/research/competitor-rea-teardown-2026-10-11.md`
> §8 (gap matrix), §9.2.

## Why

Autofill is the single most-used feature category in the field:
Simplify's entire product, the core extension feature at Teal and Huntr,
and JobRight's headline "1-click autofill across
Workday/Greenhouse/Lever/iCIMS/Ashby/Workable" (teardown §4–5). Every
competitor's tracker auto-captures applications because their extension
is already in the browser.

**Our ethic does not forbid it — it demands the shape.** PRD rule 1
bans sending without approval; filling a form is not sending. A
review-gated extension — *we fill it, you review it, you click submit* —
would be the category's trust answer to "autofill filled the wrong
fields / answered screening questions blindly" (a recurring complaint
across Simplify/Teal/Huntr reviews, teardown §5).

## The decision

One of three, recorded as an ADR in `docs/`:

1. **Build** our own extension (fill-only, review-gated).
2. **Partner** with an existing extension (if one will do fill-only
   without submit).
3. **Defer** until WO-20's outcome data proves retention, and revisit
   with evidence.

Default recommendation from the teardown sequencing: **defer** — the
funnel gaps (WO-24, WO-26) and the outcome layer (WO-20) come first, and
outcome data will tell us whether application *volume* is even our
users' bottleneck before we industrialize it.

## Constraints on whichever option wins

- Fill never submits. No exceptions, no "auto-submit for trusted
  employers" creep later.
- Extension must never read page content beyond the active application
  form (privacy posture is part of the brand).
- This is browser-extension form-fill — adjacent to, but separate from,
  the deferred Playwright ATS drivers (PRD scope boundaries: that one is
  portal applies; this one assists a human in their own browser).

## Acceptance criteria

- [ ] ADR committed in `docs/` naming the option, the rationale, and
      the revisit trigger
- [ ] If defer: the trigger is concrete (e.g., "first 100 sent
      applications via manual flow, or 10 users asking for autofill in
      cancellation/feedback surveys")
- [ ] If build/partner: a build WO is written referencing this one, and
      the fill-never-submit rule is stated in it verbatim
