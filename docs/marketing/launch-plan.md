# Launch and distribution plan

> Written 2026-08-30. Companion to `competitive-positioning.md` (the evidence)
> and WO-16 (the pricing). This document is about **getting seen**, and it is
> opinionated about sequence: the first section is a gate, not advice.

## 0. The gate — you cannot market this yet

Three facts, all verified in this repo today:

| fact | source | consequence |
|---|---|---|
| **No public URL.** Nothing is deployed. Render + Cloudflare Pages + Postgres is still an open item. | `CLAUDE.md` → Open items | There is no link to send anyone. |
| **Trial gating is not started.** WO-14 status: `not started`. | `WO-14-hunt-cadence-and-trial.md` | Every new signup scores their whole accumulated backlog on first hunt — the single most expensive moment in a user's life, spent on someone with zero commitment. |
| **Measured cost is $4.51 / active user / month.** | `PRD.md` → Success metrics | Free-in-beta is an uncapped liability. |

Do the arithmetic on the brief. "Thousands of potential users" at $4.51 with no
cap is a **$4,500/month personal bill**, arriving before a single krona of
revenue, from a product with no billing integration to stop it.

**Right now, a viral post is a threat, not a goal.**

### Gate items — all four before any public link exists

1. **Deploy.** Landing + console + backend on real infrastructure. The landing
   page is already built and good (`frontend/src/app/page.tsx`) — it is not the
   blocker; hosting is.
2. **WO-14 deliverables 2 and 3.** Stagger the first hunt; cap *scoring*, not
   display. This is written up already and the reasoning is sound — it is a cost
   fix and a retention mechanic pointing the same way.
3. **A hard signup cap.** A waitlist behind a counter. 50 seats, then a queue.
   This is not artificial scarcity theatre — it is the only thing standing
   between a good post and an unpayable invoice. It is also the honest thing to
   say: *"I can afford 50 people right now."*
4. **Instrument the PRD metrics.** Approval rate, draft acceptance, time to
   first value. `PRD.md` flags these as "a gap, not an omission." Launching
   without them means the launch teaches you nothing.

Everything below assumes the gate is closed.

---

## 1. Product Hunt — the verdict

**Not a launch channel for this product. Reconsider at 4+ countries.**

The instinct in the brief was that PH is "mostly for tech products." That is
half right, and the wrong half. Job-search tools launch on PH constantly and the
audience — 25–34, heavily employed-in-tech, in a market that has been shedding
jobs — is *full* of people who need exactly this. Category fit is fine.

**Geography kills it.** Product Hunt's audience is roughly 20% United States,
followed by India and Bangladesh
([Similarweb](https://www.similarweb.com/website/producthunt.com/),
[shno.co launch statistics](https://www.shno.co/marketing-statistics/product-hunt-launch-statistics)).
JobFinderOS serves **Sweden and the UK only**. Sweden is a rounding error in
that traffic and the UK is a modest slice.

That is not merely wasteful, it is actively damaging:

- PH ranking rewards **comment quality and early momentum**, and in 2026
  visibility is dictated by a 24-hour voting window and existing social capital
  more than product quality
  ([innmind](https://blog.innmind.com/how-to-launch-on-product-hunt-in-2026/),
  [noonlaunch](https://noonlaunch.com/blog/product-hunt-review)).
  A comment section filling with *"doesn't work in my country"* is the
  algorithmic worst case. You would be buying negative signal.
- You have **no existing social capital on the platform**. Launches now turn on
  a pre-warmed audience you do not have.
- The AI-job-tool category on PH is saturated with **precisely the products your
  positioning attacks**. Launching beside AIApply-alikes files you with them and
  forfeits the one thing that makes you different.

**When it becomes worth doing:** after US or AU coverage exists — which the
roadmap already contemplates, since Adzuna is retained as the "US/AU expansion
backbone." At that point PH's geography becomes an asset instead of a tax, and
the anti-volume angle has a saturated market to be contrarian against. Launch it
then, with the research (§3) as the story.

### The same logic disqualifies most of the tech-launch circuit

Show HN, Indie Hackers, Betalist and the rest share the geography problem. They
are worth exactly one thing: **selling the engineering story, not the product.**
A Show HN about the fabrication guard, the agency-cross-post dedupe, or running
a matching engine at $4.51/user/month is honest, interesting, and geography-free.
It recruits credibility and possibly collaborators. It will not recruit users,
and you should not measure it as if it might.

Note also the standing HN warning: if the first click requires an email, the
comments will say so. Your landing page currently sends both CTAs to `/app`,
which is behind auth. Fix that before any HN exposure — a public demo or a
sample verdict, no signup.

---

## 2. What you actually have that nobody else does

Before channels, the assets. Marketing plans fail when they list tactics that
any competitor could execute. These four are yours alone:

1. **You are the user.** Graduating Lexicon this month, job hunting in Malmö,
   with EU work rights, about to send 10–40 applications a month. Every
   competitor in `competitive-positioning.md` is a funded US company selling to
   a market it has never been in. You are building the thing you need, in the
   country you need it.
2. **The research.** Verified, sourced, dated: an F with the BBB, a Trustpilot
   integrity warning, a $2.95 trial that silently renews at $23.95, a category
   leader that is a B−. Nobody else has assembled this.
3. **A contrarian thesis with market data behind it.** The category sells
   volume at the exact moment 49% of hiring managers auto-dismiss AI resumes.
   You sell *fewer, better, never fabricated*. That is a position, not a feature
   list.
4. **Locality.** Malmö. Foo Café, Minc, Startup Dojo, Lexicon, Sydsvenskan,
   Rapidus. A Swedish product built in Sweden by someone reachable in person.

Every channel below is chosen because it converts one of these four into reach.
Anything that does not is off the list.

---

## 3. The channels, ranked

### Tier 1 — warm, geo-matched, free, and available now

**1. The Lexicon cohort and alumni network. Do this first, and do it this week.**

You graduate in August 2026. Every classmate is job hunting *right now*, in
Sweden, with a CV, in the exact situation the product was built for. It is the
highest-conversion audience that will ever exist for this product and it costs
nothing.

It also **expires**. Cohorts disperse. Ask for fifteen minutes at a cohort
session or alumni channel and demo it live against a classmate's real CV.

Target: 20–40 beta users you can hand-hold, whose feedback you can actually act
on, and whose approval-rate numbers give you the first real read on whether the
scoring bar is set right. This is the entire purpose of a 50-seat cap.

**2. Your own job hunt, published weekly.**

You are about to run your own search through your own tool. That is a content
engine no competitor can copy, because none of them are job hunting.

Post the real numbers weekly on LinkedIn — Swedish and English, LinkedIn
penetration in Sweden is high and it is where Swedish professionals actually
are: *"Week 3: 340 ads hunted, 41 cleared the bar, 6 applications sent, 2
interviews. Here is the one the scorer got wrong and why."*

Publishing the misses is what makes it credible. A tool that admits its scoring
errors in public is doing something the entire category refuses to do.

**3. Malmö in person: Foo Café, Minc, Startup Dojo.**

Foo Café runs weekly tech talks in Malmö and takes speaker submissions; Minc is
the city's startup house with 5+ open events a month; Startup Dojo has run
monthly since 2011. You are local to all three.

Submit a talk. Not a product pitch — **"I read every one-star review of every AI
job-application tool."** That gets accepted on its own merits and puts you in a
room of exactly the right people, several of whom are hiring or job hunting.

### Tier 2 — earned media, powered by the research

**4. Publish the billing investigation on your own domain. This is the single
highest-leverage asset you have, and it is already 80% written.**

`competitive-positioning.md` is a consumer-protection story wearing a
positioning document's clothes. Published as a neutral, sourced comparison it:

- **Ranks for high-intent commercial queries** — "AIApply review", "LazyApply
  refund", "JobCopilot alternative", "Sonara cancel". These are searched by
  people with a card in their hand and doubt in their mind. That is the cheapest
  qualified traffic in this category and it compounds.
- **Is genuinely linkable.** Journalists covering AI hiring need exactly this
  table.
- **Survives Reddit**, which nothing promotional does. A sourced comparison
  posted as a public service is tolerated where a product link is banned.

> **Handle this carefully — it is the one item here with real downside.**
> You are publishing factual claims about named companies' billing practices.
> Non-negotiables: re-verify every figure at source on publication day (the
> existing doc already warns these have a shelf life), link every claim,
> date-stamp the page, restrict yourself strictly to what is documented in
> public review platforms and BBB records, and **disclose your competing
> interest in the first paragraph.** Describe what reviewers report, not what
> you conclude about the companies' intent. Stale or overstated claims about a
> named business are a legal problem, not a marketing problem.
>
> The same caution applies to the AI-resume statistics: your own doc already
> flags that most originate from resume-writing services with an obvious
> interest in the finding. Cite them as claims with sources attached, never as
> settled fact.

**5. Swedish trade and local press.**

Free to pitch, reaches your exact users:

- **Union member magazines** — *Kollega* (Unionen, ~700k members), *Ingenjören*,
  *Publikt*. Swedish union density is roughly 70%; these publications run
  practical job-hunting content continuously and reach vastly more Swedish job
  seekers than any tech blog will.
- **Skåne local and tech press** — Sydsvenskan, Rapidus. "Malmö student builds
  Swedish alternative to US job-hunting apps" is a local story, and the billing
  research gives the journalist their angle for free.

Pitch the story, not the product. The product is the last paragraph.

### Tier 3 — Reddit, and only where it works

**6. UK first. Sweden barely at all.**

The UK is Reddit-native for job hunting; r/UKJobs and r/RecruitmentHell are
active and directly on target. Sweden is not — Swedish job seekers are on
Facebook and LinkedIn, and effort spent on r/sweden is effort wasted.

Reddit rules are strict and enforced: 61% of the subreddits founders pitch into
ban self-promotion outright
([OneUp study](https://oneup.today/blogs/reddit-selfpromo-rules-study-2026)),
the 90/10 participation rule applies, affiliation must be disclosed, and
cross-posting the same promotional text is a fast way to get banned
([redship](https://redship.io/blog/reddit-self-promotion-rules)).
Vote manipulation and unsolicited DMs are sitewide-suspension offences.

So: participate genuinely for several weeks before posting anything of your own,
read each subreddit's live rules rather than trusting any summary, post from
your personal account with the affiliation stated, lead with the research rather
than the product, and write each post fresh.

**7. Swedish Facebook groups.**

This is where Swedish job seekers actually are — regional "Lediga jobb
Skåne/Malmö" groups run to tens of thousands of members. Admin-gated: message
the admins first and ask. One yes from a large group admin outperforms every
tech-launch platform in this document combined, for this market.

---

## 4. The strategic gap worth closing — the a-kassa wedge

Flagging this as the highest-value idea in this document, though it is a
**product** suggestion rather than a marketing tactic, and it is outside what
you asked for — so it is a recommendation, not something I have acted on.

Swedish unemployment insurance requires claimants to report job-seeking activity
to Arbetsförmedlingen on a recurring basis — which jobs, which employers, when.
It is a real, recurring, tedious compliance burden carried by every unemployed
person in the country.

**JobFinderOS already stores every application forever**, with date, employer,
and the exact documents sent. You are one export away from producing that report
as a by-product of using the tool.

Why this matters more than any channel above:

- **No competitor can copy it quickly.** It requires Swedish domain knowledge
  that a US company does not have and will not prioritise for a market this size.
- **It sells to people who do not care about AI.** The pitch stops being "AI
  matches your CV" and becomes "your activity report writes itself."
- **It unlocks institutional distribution.** A-kassor and unions have direct
  channels to hundreds of thousands of job seekers, and a compliance-reducing
  tool is something they can recommend without endorsing a hype product.

**Verify before building:** I have not confirmed the current exact format or
cadence of the aktivitetsrapport, and the rules change. Check Arbetsförmedlingen's
current published requirements before committing engineering effort.

---

## 5. Sequence

Anchored to the constraint that matters: **the Lexicon cohort disperses within
weeks.**

| when | do | target |
|---|---|---|
| **Before anything** | Close the §0 gate: deploy, WO-14, 50-seat cap, instrumentation | A link that exists and cannot bankrupt you |
| **Week 1–2** | Lexicon cohort demo. Fix the landing CTA so the first click is not a login wall. | 20–40 real users with real CVs |
| **Week 2 onward, ongoing** | Weekly public hunt log — LinkedIn, SE + EN, including the scorer's misses | Compounding credibility |
| **Week 3–6** | Publish the billing investigation, fully re-verified and disclosed. Submit the Foo Café talk. | The evergreen SEO and press asset |
| **Week 6–10** | UK Reddit, after genuine participation. Swedish Facebook groups via admins. | First cold users; the cap starts to bite |
| **Month 3+** | Union magazines, Skåne press. Raise the cap only if unit economics hold. | Institutional reach |
| **Not yet** | Product Hunt, Show HN as a *product* launch | Revisit at 4+ countries |

## 6. What to measure

The PRD's metrics are the right ones and none are instrumented. Until they are,
every number below is a guess.

The one that decides everything is **approval rate**, target 15–40%. Below 15%
the scoring bar is too low and you are shipping noise; above 40% it is too high
and you are hiding jobs. Get that reading from the Lexicon cohort *before*
spending any effort on cold channels — marketing a product whose core filter is
mis-tuned just distributes the disappointment faster.

Per channel, track only: signups, and of those, how many reach a first approved
match. A channel that sends 500 people and produces 3 approvals is worse than
one that sends 12 and produces 8.

## Sources

- [Similarweb — producthunt.com audience](https://www.similarweb.com/website/producthunt.com/)
- [shno.co — Product Hunt launch statistics 2026](https://www.shno.co/marketing-statistics/product-hunt-launch-statistics)
- [innmind — How to launch on Product Hunt in 2026](https://blog.innmind.com/how-to-launch-on-product-hunt-in-2026/)
- [noonlaunch — Product Hunt review 2026](https://noonlaunch.com/blog/product-hunt-review)
- [OneUp — self-promotion rules across 49 subreddits](https://oneup.today/blogs/reddit-selfpromo-rules-study-2026)
- [redship — Reddit self-promotion rules 2026](https://redship.io/blog/reddit-self-promotion-rules)
- [Nordic Startup Hub — Sweden communities and media](https://nordicstartuphub.com/swedenmedia)
- Internal: `docs/marketing/competitive-positioning.md`, `PRD.md`, `ROADMAP.md`, `WO-14`, `WO-16`
