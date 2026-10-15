# Landing copy teardown — competitor heroes, formulas & the users' own words

> **Question this doc owns:** what landing copy should JobFinderOS ship,
> grounded in (a) what the category's winners actually say and (b) the
> language real users use in reviews of those tools — the input WO-30's
> rewrite is gated on.
> **Method:** REA v6.5.0 (`rea-agents`, morluto/rea) `capture-browser-scenario`
> — bounded Playwright runs, headless Chrome 155, **public unauthenticated
> page loads only**, accessibility-tree text captured per page. Captured
> 2026-10-15. Raw Evidence JSONs + extracted text in
> `docs/research/artifacts/rea-copy/` [C1–C11]. Teal's landing was
> Cloudflare-blocked for headless [C5] — its Trustpilot review page
> [C10] and the 2026-10-13 desk audit cover what we need. Trustpilot
> for aiapply.io returned a near-empty page [C7] — AIApply's review
> base is elsewhere (BBB per the 2026-10-11 teardown); not re-chased.

---

## 1. Evidence inventory

| ID | Target | What was captured |
|---|---|---|
| C1 | `aiapply.co/` (landing) | Full accessibility copy: hero, demo walkthrough, features, pricing FAQ |
| C2 | `jobright.ai/` (landing) | Full copy: hero, four-feature blocks with quantified claims, stats band |
| C3 | `careerflow.ai/` (landing) | Hero, tools list, testimonial strip |
| C4 | `simplify.jobs/` (landing) | Hero, quantified stats band, live-roles section |
| C5 | `tealhq.com/` | **Blocked** (Cloudflare bot wall) — see note above |
| C6 | `lazyapply.com/` (landing) | Hero, feature blocks, demo placeholders |
| C7 | `trustpilot.com/review/aiapply.io` | Near-empty shell; review base is on BBB [Desk 2026-10-11] |
| C8 | `trustpilot.com/review/lazyapply.com` | Review corpus — the billing/quality pain tier |
| C9 | `trustpilot.com/review/jobright.ai` | Review corpus — 4.8/3,609 reviews, the closest competitor |
| C10 | `trustpilot.com/review/tealhq.com` | Review corpus — tracker-tier language |
| C11 | `trustpilot.com/review/careerflow.ai` | Review corpus — mixed |

## 2. The hero formulas, verbatim [C1–C4, C6]

| Competitor | H1 | Sub | CTA | The formula |
|---|---|---|---|---|
| **AIApply** | "Stop Applying for Weeks — Start Interviewing in Days" | "AIApply finds high-match roles, tailors your resume & cover letter, auto-applies, and coaches you live — so you move from submit to getting a job fast." + banner "🏆 61% of users get an interview in first 10 days" | "Start now" | **Pain → time-boxed outcome.** Contrast verbs (applying/interviewing), concrete timeframe (days), stat banner above the fold |
| **JobRight** | "No More Solo Job Hunting — Do it with AI" | "Get matched jobs, autofill applications, tailored resume, and recommended insider connections in less than 1 min!" | "Try For Free" | **Emotional isolation → companion.** "Solo" is the pain word; "with AI" the companion; sub quantifies time (<1 min) |
| **Simplify** | "Get hired 3x faster with your AI job search partner." | "Simplify proactively matches better-fit roles, customize your resume, and applies for you." + "4.9/5 Trusted by 2M+ job seekers" | "Try for free" | **Multiplier + partner.** The only hero that puts the number IN the H1 |
| **Careerflow** | "Land your dream job. Without the stress." | "AI Resume Builder · Automated Job Tracking · Optimize your LinkedIn Profile · And Much More..." + "Trusted by OVER 1.2 million JOB SEEKERS!" | "Sign up FOR FREE" | **Aspirational outcome → objection removal.** Two sentences: dream, then the feeling removed |
| **LazyApply** | "Automate Your Job Application Process" | "Our AI Job search tool automatically apply to all the jobs on platforms like Greenhouse, Dice, Indeed and Ziprecruiter using Job GPT." | "Get Started" | **Pure automation.** Weakest hero of the five — describes the mechanism, not the outcome |

**Common patterns (all five):**
1. **The H1 is outcome or feeling, never mechanism.** "Get hired", "Land your dream job", "Start Interviewing" — nobody leads with what the product does.
2. **A number lives in or beside the hero** — 61%, 3x, <1 min, 1.2M, 2M+. We cannot invent one; our defensible numbers are *time-to-first-matches* (hours, not days) and *coverage* (every public listing in Sweden).
3. **The sub is a feature list wearing a sentence** — every competitor stuffs 3–5 features into it. Ours already does this; the audit's complaint is the H1, not the sub shape.
4. **Trust line rides the fold** (users count / rating / logos). Ours: "No card · no credits · nothing sent without you" — trust-as-terms instead of trust-as-crowd. Right call at 3 beta users; keep.

## 3. The users' own words — review corpus [C7–C11]

### 3.1 What users PRAISE (language we can echo honestly)

From JobRight (4.8★), Teal, LazyApply, Careerflow reviews:

- **Relevance is the killer feature.** *"one of the best tools for finding
  relevant job postings. Much better than LinkedIn, Simplify, Indeed"* ·
  *"Gives actual relevant jobs"* · *"ranking postings based on your
  skills"* — relevance is the word users reach for, unprompted.
- **The feeling of structure.** Teal: *"Helps me feel like my job search
  is a job in itself with its own tool that I come to"* · *"I can clearly
  see which jobs I need to apply for"* — the anxiety killer is
  *visibility*, not volume.
- **Offloading the remembering.** *"Helps me offload the whole
  remembering to apply, circling back to intriguing jobs, etc. so I can
  focus on applying"* — exactly our twice-daily loop, in a user's words.
- **Resume tailoring that respects the source.** JobRight's *"AI resume
  tweaker"*, *"make one's resume suitable for each job application"* —
  users value per-job tailoring; nobody praises invention.
- **Free-tier respect.** *"Doesn't require a subscription to use their
  tools"* · *"Like that it is free to use"* — goodwill language our
  free-beta terms can borrow directly.

### 3.2 What users DAMN (pain language our hero can position against)

- **Auto-apply quality collapse.** LazyApply: *"felt the platform made
  consistent mistakes when applying for jobs for me"* · *"it get[s] hung
  up on things as simple as 'Enter Today's Date'"* · *"500 apps, 0
  interviews"* (JobCopilot, 2026-10-11 teardown) — **volume without
  judgment is the category's open wound.**
- **Billing betrayal.** *"I asked for a refund within the 30-day window…
  it took two weeks"* · *"software returns errors… 3 weeks waiting for
  customer support"* — trust language matters more than feature language
  in this category.
- **The black-box fear.** The 2026-10-11 teardown: AI-written CVs make
  skills "harder to verify" — employers side. Users side: applications
  that don't reflect them.

### 3.3 The positioning gap all six heroes leave open

Every hero sells **more**: more applications, faster applications,
applied-for-you. Every review pain is **quality and trust**: wrong
answers, invented content, billing traps. **Nobody's hero owns
judgment** — "the right few, chosen for you, nothing sent without you."
That is our honest differentiator AND the users' own pain language.

## 4. Recommended hero (evidence-built)

**Formula:** outcome-feeling H1 (Careerflow pattern) + time-boxed
promise sub (AIApply pattern) + relevance word from reviews + our
defensible numbers only.

### EN

> **H1:** Stop sending applications into the void.
> **H1 line 2 (amber):** Start getting taken seriously.
> **Sub:** JobFinderOS scans every public job listing in Sweden and the
> UK twice a day, scores each one against your CV, and explains every
> verdict — the skills you have, the gaps, what transfers. Your first
> matches are waiting tomorrow morning. Nothing is ever sent without you.
> **CTA:** Upload your CV — free in beta
> **Proof line (unchanged):** No card · no credits · nothing sent without you

*Why each piece:* "into the void" is the emotional pain under every
1-star volume review; "taken seriously" is the outcome users describe
when relevance works ("recruiters reaching out", "actual relevant
jobs"); "every public listing… twice a day" is our defensible coverage
claim; "explains every verdict" is the judgment gap nobody owns;
"tomorrow morning" is our honest time-box; the last sentence is the
anti-LazyApply trust line.

### SV (native, no jakt-metaphor)

> **H1:** Sluta skicka ansökningar ut i tomma intet.
> **Rad 2 (amber):** Börja bli tagen på allvar.
> **Sub:** JobFinderOS bevakar alla offentliga platsannonser i Sverige
> och Storbritannien två gånger om dagen, poängsätter varje annons mot
> ditt CV och förklarar varje bedömning — kompetensen du har, luckorna,
> vad som överförs. Dina första matchningar väntar imorgon bitti. Inget
> skickas någonsin utan ditt godkännande.

*Swedish note:* "tomma intet" carries the void feeling idiomatically;
"tagen på allvar" (taken seriously) is natural and strong; the sub uses
*bevakar* (monitors/watches over) — the natural Swedish for a
twice-daily scan, replacing all jakt language per the owner's direction.

### Runner-up (if "void" reads too dark for SE)

> **H1:** The jobs worth applying to, found for you while you sleep.
> **Sub:** Every public listing in Sweden and the UK, scanned twice
> daily and scored against your CV — with the verdict explained, and
> nothing sent without you.

## 5. Secondary copy the corpus upgrades (WO-30 scope)

- **Stats band** → replace "2× runs every day" with the users' metric:
  "2× varje dag" stays, but add the review-echo line *"relevant jobs,
  not more jobs"* as the band's caption (Teal/JobRight reviewers'
  phrase).
- **Comparison table row 1** "What gets sent" — append the review quote
  as proof-source framing: users' words "consistent mistakes when
  applying for jobs *for me*" are why approval gates exist.
- **Proof slot** note: when the first beta quote is collected, mine the
  reviewer vocabulary above (relevance / taken seriously / offload the
  remembering) — that is the register users respond in.
- **FAQ "Will employers know I used AI?"** — strengthen with the
  review-side fear: users worry applications "don't reflect them";
  our answer already covers it.

## 6. What we deliberately did NOT copy

- Inflated user counts (2M+, 1.2M, 3M) — unverifiable for us, and the
  honest-placeholder pattern already ships.
- "3x interviews" multipliers — no denominator exists at 3 beta users.
- "Auto-applies for you" anything — the category's #1 review pain and
  against the product ethic.
- Insider-referral promises — not in the product.
