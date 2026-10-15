# Competitor REA teardown — AIApply, JobRight + desk pass on the field

> **Question this doc owns:** what makes the top AI job-search apps succeed,
> and where must JobFinderOS change to compete — checked against the WO backlog.
> **Method:** REA v6.4.0 CLI (morluto/rea), passive CDP attach to a user-owned
> headless Chrome 154, **public unauthenticated page loads only** (same
> discipline as the SoloAdventurerWeb teardown). Captured 2026-10-11.
> Raw evidence JSONs in `docs/research/artifacts/rea/` [E1–E3].
> Desk research (reviews, pricing aggregators) tagged **[Desk]** — several
> sources are competitor marketing content; comparative claims there are
> directionally useful only.
> **What this adds:** bundle-level confirmation of how the leaders' growth
> machines are actually built (free-tool farms, programmatic SEO, AI-search
> optimization), which app-store listings and press do not show.

---

## 1. Evidence inventory

| ID | Target | What was captured |
|---|---|---|
| E1 | `aiapply.co/` (landing) | 27 scripts parsed, 0 failures; chunk edges; endpoint `/static/m/t` (Matomo-style self-hosted analytics); Cloudflare; Vite build; Google Consent Mode **default denied** [E1] |
| E2 | `aiapply.co/app/jobs` → redirects to `/signin?returnUrl=…` | 11 shell scripts; endpoints `/csrf-token`, `/api/auth/check` — code-split SPA, app chunks load **post-auth only** [E2] |
| E3 | `jobright.ai/` (landing) | 1 script — fully server-rendered; assets on `static.jobright.ai` (Next.js); no client route tree exposed [E3] |
| E4 | `aiapply.co/llms.txt`, `jobright.ai/llms.txt` | full site inventories, publicly served for AI agents — quoted extensively below |
| E5 | `jobfinderos.com` | single landing page + `/sv` + `/privacy` + `/app`; **no llms.txt (404)**, no content pages, no free tools |

---

## 2. The market map (who we actually compete with)

Three tiers, and they win differently:

| Tier | Players | What they sell | Their weak point (from reviews [Desk]) |
|---|---|---|---|
| **Volume auto-apply** | AIApply, JobCopilot, LoopCV, Sonara, LazyApply | "We apply for you" — credits/subscriptions per application | **Billing trust**: AIApply F BBB (credits-on-top), Sonara €2.95 trial → €23.95/mo, LazyApply 2.1★ refunds. **Submission quality**: wrong screening answers, "500 apps, 0 interviews" (JobCopilot ~2★ UK Trustpilot) |
| **Tracker + toolkit** | Teal, Huntr, Simplify, Careerflow | Free tracker/autofill, paid resume AI ($9–40/mo) | Autofill reliability on non-standard ATS; paywall resentment (Teal $13/**week** criticism); Huntr free capped at 100 jobs |
| **AI-native match + copilot** | **JobRight** (closest to us) | AI matching + tailored resume + 1-click autofill + referral insights + own job index | US-centric; H1B-focused content niche |

Everyone's real acquisition engine is the same: **free surface → funnel**, not paid ads.

---

## 3. AIApply — how the machine is actually built (E1, E2, E4)

**The product surface (from their own llms.txt, shipped for AI agents):**

- **30+ free, no-signup tools** — resume optimizer, ATS checker, STAR-method
  generator, salary calculators, follow-up email generator, skills-gap
  analyzer, career aptitude test… each a standalone SEO page with utility.
- **Programmatic content by job title**: `resume-examples/<role>`,
  `cover-letter-examples`, `salaries`, `skills`, `careers` — thousands of
  per-role pages.
- **14 locales including Swedish** [E4] — direct evidence the SE market is
  worth serving for them; `resume-translator` product for international
  applications.
- **Full workflow suite**: resume builder (with
  `resume-builder/from-linkedin` — **LinkedIn URL → instant resume**),
  cover letters, **Auto Apply** (credit packs, e.g. 100/250, sold *on top
  of* the ~$29/mo subscription [Desk]), **Interview Answer Buddy**
  (real-time answer suggestions during *live* interviews), resume scanner,
  translator.
- **AI-native SEO beyond Google**: `llms.txt` **plus an OKF (Open Knowledge
  Format) knowledge graph** (`/okf/index.md`) — they are optimizing to be
  *recommended by ChatGPT/Claude/Perplexity*, a channel almost nobody in
  this category touches.
- **Growth loops**: influencer/affiliate program; student verification
  discount; "372,241+ roles applied to" social proof; 4.3★/1,773 reviews
  aggregate [Desk].

**Bundle-level findings:** landing is a light Vite SPA (marketing shell);
the app at `/app/jobs` is auth-gated with code-split chunks — nothing
sensitive ships unauthenticated. Self-hosted Matomo-style analytics
(`/static/m/t`), Cloudflare, Google Consent Mode defaulting to denied
(strict-cookie posture is cheap and EU-friendly) [E1].

**The pattern:** the *app* is ordinary. The **funnel is the moat** — every
free tool and example page is a permanent acquisition asset that compounds.

## 4. JobRight — the closest analog (E3, E4)

- **Own job index as the product AND the SEO**: public `/jobs/info/<id>`
  pages crawled from "200K+ company career sites, updated hourly" — every
  job posting is a landing page ("no expired listings" as a selling point).
  `/remote-jobs` — curated verified-remote board, same play.
- **Two-sided**: `/employers` — "only qualified, active candidates apply,
  no noise" — they monetize recruiters on the demand side too (Simplify's
  revenue model as well, [Desk]).
- **Referral insights** (`/job-referral`): "4x interview chances" via
  personalized connection suggestions + outreach templates.
- **1-click autofill** across Workday/Greenhouse/Lever/iCIMS/Ashby/Workable.
- **Funnel discipline**: signup path is `/onboarding-v3/signup` — the
  onboarding flow is *versioned*; they are on at least their third
  iteration. WordPress blog targets the H1B/visa-sponsorship niche
  (international workers in the US) — a specific, desperate, high-value
  segment.
- Fully server-rendered landing (1 script) — nothing leaks client-side.

## 5. The rest of the field, quickly [Desk]

- **Simplify**: free extension (autofill + tracker) is the whole funnel;
  revenue from recruiter side. Buggy autofill is the recurring complaint.
- **Teal**: free unlimited tracker (most generous) → Teal+ $13/wk / $29/mo;
  weekly billing is the #1 resentment theme. 9/10 review scores for the
  free plan.
- **Huntr**: free Kanban tracker capped at 100 applications + autofill;
  Pro ~$40/mo seen as steep.
- **Careerflow**: $8.99/wk / $23.99/mo; strong LinkedIn-optimization angle.
- **LoopCV**: $19.99–$89.99/mo tiers incl. "Done For You"; ~4.0★ but
  recruiter-email auto-apply carries wrong-answer/spam risk.
- **Sonara**: relaunched after shutting down in 2024 (~5.4% interview rate
  disclosed); trial→rebill pattern is the top Trustpilot complaint.
- **Europe-specific gap**: Kitsuno's "Best AI Job Search Tools for Europe"
  round-up exists because the US tools under-serve Europe — the tools
  listed for Europe are still US products with weak local inventory.

---

## 6. What actually makes them successful (the mechanisms)

1. **Free surface as the acquisition engine.** Nobody pays CAC for job
   seekers. Every winner converts from free utility (tools, tracker,
   extension, public job pages) into the paid funnel.
2. **Programmatic SEO at scale.** Per-role/per-job pages × locales = tens
   of thousands of permanent landing pages (AIApply examples library,
   JobRight job pages).
3. **AI-search optimization.** llms.txt + OKF knowledge graphs — being the
   answer ChatGPT gives when someone asks "best tool to find a job in
   Sweden".
4. **Instant pre-signup value.** LinkedIn URL → resume in seconds
   (AIApply); the moment of value happens *before* the account exists.
   JobRight versions its onboarding like a product (v3).
5. **Whole-workflow lock-in.** Resume + cover letter + tracker + autofill
   + interview prep in one account — switching costs grow with usage.
6. **Weekly billing as price capture.** $9–23/week across Teal, AIApply,
   Careerflow, Sonara — hated and effective. The category's revenge
   opportunity is clean billing (already decided: WO-16/WO-17).
7. **Both sides where possible.** JobRight and Simplify monetize
   recruiters/employers, subsidizing the seeker side.

## 7. Where they are uniformly weak — our openings

| Weakness | Evidence | Our answer (status) |
|---|---|---|
| **Billing distrust** | AIApply F BBB; Sonara trial→rebill; LazyApply 2.1★; Teal weekly-billing resentment [Desk] | WO-16 clean pricing + WO-17 cancel-first feedback — **already decided, keep P1** |
| **Blind auto-apply quality** | Wrong screening answers, mismatched resumes, "500 apps 0 interviews" [Desk] | Two-gate human approval is our *product thesis*, not just an ethic (PRD rule 1) |
| **Zero outcome honesty** | Nobody shows whether applications worked; volume metrics only ("372k applied!") | **WO-20 outcome layer** — replies, statuses, real denominators. Our launch weapon |
| **Fabrication fear** | "Dangers of fake resumes" content ranks; AI resume tools inventing facts is a known objection | Zero-fabrication guard (WO-01/23) is marketable, not just compliance |
| **US-centricity** | H1B content, US boards, US salaries dominate; Europe round-ups exist because of the gap [Desk] | SE+UK focus, profession-agnostic, Swedish UI — genuine white space (AIApply's Swedish locale shows the demand) |
| **Expired/dead listings** | JobRight sells "no expired listings" because the category is full of them | WO-22 apply-link recovery + 30d freshness gate |

---

## 8. Gap matrix — JobFinderOS vs the field

| Capability | AIApply | JobRight | Teal/Huntr/Simplify | **Us** |
|---|---|---|---|---|
| AI match → queue → human gate | partial (auto) | ✅ | — | ✅ **built** (our differentiator) |
| Grounded tailoring, zero-fabrication guard | ❌ | ❌ | ❌ | ✅ **built** (WO-01/23) |
| Outcome tracking (replies/statuses) | tracker only | tracker only | tracker only | 🟡 WO-20 open |
| Sent forever / retrievable | ✅ | ✅ | ✅ | ✅ built |
| Apply link quality | 🟡 | ✅ (hourly refresh) | 🟡 | 🟡 WO-22 open |
| Employer-side monetization | ❌ | ✅ | ✅ (Simplify) | ❌ (TalentHive is adjacent, not coupled) |
| **Free tool / content funnel** | ✅ massive | ✅ massive | ✅ free tiers | ❌ **single landing page** |
| **AI-search presence (llms.txt/OKF)** | ✅ | ✅ | ❌ | ❌ |
| LinkedIn import (onboarding de-cliff) | ✅ | — | 🟡 | ❌ |
| Browser autofill extension | 🟡 auto-apply | ✅ | ✅ core feature | ❌ deferred (Playwright drivers deferred too) |
| Interview prep | ✅ (live coaching) | — | — | ❌ PRD-deferred (keep deferred) |
| Multi-locale | 14 incl. Swedish | EN | EN | 🟡 /sv exists, i18n gap flagged in docs |
| Swedish/UK market depth | 🟡 locale skin | ❌ | ❌ | ✅ native (sources, dedupe, freshness) |

**Honest read:** we have the *highest-trust core pipeline* in the field and
the *weakest acquisition surface*. Competitors have weak cores and
industrial funnels. Their funnel is copyable in months; our core (grounded
tailoring, gates, outcome honesty, SE/UK inventory depth) is not quickly
copyable by them. The strategy that follows: **keep the trust core as the
product, build the minimum credible funnel, and sell the trust difference
explicitly.**

---

## 9. Recommendations

### 9.1 Confirm / re-prioritize existing WOs (no new scope)

- **WO-20 outcome layer → raise to first among P1s.** It is simultaneously
  our best marketing asset (nobody can claim it), our review engine, and
  our cancel-survey truth source.
- **WO-16 + WO-17 stay P1** — the category's F-BBB wound is our cheapest
  differentiator ("the job tool that doesn't rob you" positioning writes
  itself).
- **WO-15 career-site discovery** — JobRight's "200K company career sites,
  hourly" validates direct-from-employer inventory as a *headline feature*,
  not just an inventory hedge. Keep P2 but expect it to become marketing.
- **WO-22** — dead-link recovery maps directly to the "no expired
  listings" trust claim.

### 9.2 Proposed new work orders

**WO-24 — Pre-signup value layer (kills the onboarding cliff).**
(WO file: `WO-24-pre-signup-value-layer.md`.)
Free, no-account tools that reuse existing machinery:
1. *CV-koll / CV scan* — upload CV + paste a job ad → match score with
   grounded gap list (reuses the matcher; output-only, no write path).
   This is AIApply's Resume Scanner + their entire free-tool thesis in
   one page, but **grounded in facts** — none of theirs can promise that.
2. *LinkedIn URL → parsed profile/CV draft* (AIApply proves demand;
   directly attacks the cliff where Lorna stalled).
Each tool page is simultaneously product surface, SEO surface, and the
account-creation moment. Rationale: every competitor's growth engine
(§6.1, §6.4); our beta blocker (onboarding cliff) from a different angle
than WO-23's in-app recovery.

**WO-26 — AI-search + content surface (llms.txt + OKF + per-role pages).**
(Numbering updated 2026-10-15: WO-25 was taken by the migration drift
guard after the 2026-09-28 outage. WO file: `WO-26-ai-search-content-surface.md`.)
Ship `/llms.txt` and an OKF bundle declaring JobFinderOS's concepts
(honest matching, grounded tailoring, two gates, outcome tracking) —
AIApply is the only player doing OKF today; the "AI job tool for Sweden/
Europe" answer-slot is nearly uncontested. Seed per-role pages from our
own live pool (real Swedish/UK titles from Platsbanken/Reed), not
fabricated templates: "what <role> CVs get scored on in Sweden 2026" is
content only we can generate truthfully. Swedish + English.

**WO-27 — Review-gated autofill extension (decision WO, not a build WO).**
(WO file: `WO-27-autofill-decision.md`.)
Autofill is the single most-used feature category across Simplify /
Teal / Huntr / JobRight. Our ethic doesn't ban it — it demands it be
**review-gated**: fill the form, never submit; the human submits. Decide:
build vs partner vs defer until WO-20 ships. (Numbering updated 2026-10-15: the
floated fabrication-guard WO-26 was absorbed by WO-23; WO-25 was taken by
the migration drift guard.)

### 9.3 What NOT to copy

- **Credits-on-top billing, weekly billing, trial→rebill** — the category's
  review-score wound. (Already decided; recorded here so nobody
  "rediscovers" it.)
- **Blind auto-apply volume** — it's their churn engine and our antithesis.
- **Interview prep now** — PRD-deferred; revisit only after outcome layer
  proves retention (interview prep is the natural next sell *to users our
  outcome data says are converting*).
- **Employer-side monetization** — real money (JobRight/Simplify) but a
  different company. TalentHive remains the vehicle.

### 9.4 Sequencing recommendation

WO-20 → WO-24 → WO-16/17 (billing ships when there's something worth
paying for repeatedly) → WO-26 → WO-27 decision. Rationale: outcome data
makes every later step sellable; the free-tool layer is what feeds trials;
pricing/cancellation polish converts and retains; content/AI-search
compounds on top of a product that now has proof.

---

## 10. Evidence references

- [E1] `artifacts/rea/aiapply-bundle-analysis.json` — landing bundle, 27 scripts
- [E2] `artifacts/rea/aiapply-app-bundle.json` — signin shell, endpoints
- [E3] `artifacts/rea/jobright-bundle.json` — SSR landing, 1 script
- [E4] `aiapply.co/llms.txt`, `jobright.ai/llms.txt` (public, fetched 2026-10-11)
- [Desk] FastApply review (AIApply 4.3★/1,773, F BBB), RemoteJobAssistant
  11-tool test, Kitsuno Europe round-up, Trustpilot pages (Sonara, LoopCV,
  JobCopilot UK), Teal/Huntr/Simplify comparison round-ups — see chat
  summary of 2026-10-11 for links.
