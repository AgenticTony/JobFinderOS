# Competitor tech stacks — passive reconnaissance

> Gathered 2026-08-31 from data the companies publish themselves: HTTP response
> headers, public DNS records, HTML served to any visitor, public GitHub
> organisations. No authentication was used, nothing was probed, no endpoint was
> enumerated. Everything here is re-runnable with the commands in §4.
>
> Companion to `competitive-positioning.md` (billing and market position). That
> document is the *what they sell*; this is the *what they built it with*.
>
> **Shelf life:** infrastructure moves. Re-verify before relying on any row.

## 1. The findings

| | marketing site | product / app | API | billing | AI vendor (verified) | email |
|---|---|---|---|---|---|---|
| **AIApply** | Squarespace + Shopify assets, Cloudflare, PostHog **EU** | Laravel 12 + Vue 3 + Tailwind 4 + MySQL 8, Docker | — | — | — | Google Workspace + Amazon SES, self-hosted MX |
| **JobCopilot** | **WordPress** (Elementor `hello-elementor` + TranslatePress) on **Hostinger / LiteSpeed** | `app.` → Next.js on **AWS CloudFront**; separate `auth.` and `admin.` distributions | `api.` behind Cloudflare | — | — | **Microsoft 365** + Brevo + Amazon SES |
| **Jobright** | Next.js | Next.js, `static.` on CloudFront | `api.` on **AWS EC2, us-west-2** | — | **OpenAI** (`openai-domain-verification`) | Google + **Mailjet**; `_spf.wpcloud.com` (WordPress.com) still in SPF |
| **Simplify** | Next.js on **Vercel** (`x-vercel-cache`, prerendered) | same | `api.` on **Google Cloud** (`Server: Google Frontend`) | — | **Anthropic** (`anthropic-domain-verification`) | Google Workspace + **Help Scout** |
| **LazyApply** | **Netlify** | `app.` → `lazyapply-extension-dashboard.netlify.app` — the product is a **browser extension**, the web app is just its dashboard | — | **Paddle** (`paddle-verification`) | — | Google Workspace |
| **Sonara** | did not respond (HTTP/2 `INTERNAL_ERROR`, 0 bytes) on two attempts | — | — | — | — | Google Workspace |

Also visible: Simplify runs **Ahrefs** and **1Password**; AIApply runs PostHog on the
**EU** cloud (a deliberate data-residency choice); JobCopilot verifies **Postman**.

### Caveats that matter

- **A domain-verification TXT record proves an account, not production use.** The
  OpenAI record on Jobright and the Anthropic record on Simplify are strong
  evidence, not proof, that those are the inference vendors.
- **Marketing stack ≠ product stack**, and JobCopilot is the proof: an Elementor
  WordPress site on budget shared hosting fronting a Next.js app on AWS. Reading
  only the apex domain would have got this badly wrong.
- Sonara not responding is one observation on one day from one network. It is
  consistent with the product being wound down, but it does not establish it.

## 2. The one that actually matters — AIApply's take-home

AIApply publishes two hiring assessments at `github.com/aiapply`. The second,
`auto-apply-job-matching-assignment` (created 2026-08-14), is described as *"a
scaled-down version of the problem we work on every day."* It discloses more
about their architecture than every header in §1 combined:

- **Corpus scale, stated by them:** *"in production this corpus is over a million
  postings and grows every hour."*
- **Search engine: Meilisearch.** It is the only pinned dependency
  (`meilisearch==0.37.0`) and the task supplies a running instance.
- **Python 3.12** for the matching pipeline; **Laravel 12 + Vue 3 + MySQL 8** for
  the application (from the sibling `laravel-assessment` repo).
- **Required shape:** `ingest.py` → `search.py --profile p1` → `results.json`.
  Profile in, ranked shortlist of ~10 out.

**The strategic read, and the reason this file exists:**

Their pipeline as specified is **retrieval and ranking over a million-document
index. There is no per-job LLM call in it.** JobFinderOS does the opposite —
`glm-5.1` at temperature 0 against every job that clears the cheap gates, ~6s a
call, with an anchored rubric and a dead-band re-score.

Neither is wrong; they are different products. But it names the trade honestly:

- **They optimise for recall at scale.** A million postings, cheap ranking, ~10
  shown. Marginal cost per user approaches zero, which is what makes $29/month
  with credit packs work.
- **We optimise for judgement per posting.** Every kept job gets a real verdict
  with skills, gaps and transfer — the thing the landing page calls *"every match
  explains itself."* That is defensible product differentiation and it is also
  the entire $4.51/user/month cost line.

Two consequences worth holding on to:

1. **The cost asymmetry is structural, not a tuning problem.** Do not expect to
   close it by prompt-shrinking. The existing cheap-gate architecture
   (location/language/freshness before scoring) is the right shape and is the
   thing to keep investing in — it is what stops the LLM cost scaling with the
   corpus.
2. **Meilisearch is a real signal for the scaling path.** If the pool ever
   outgrows Postgres queries at the gate stage, a retrieval layer in front of
   scoring is the proven move in this category — narrowing the candidate set
   cheaply so the expensive verdict runs on fewer, better jobs. Not urgent;
   worth knowing the incumbent already lives there.

## 3. A marketing finding that came out of this

`github.com/jobright-ai` hosts **36 repositories** that are not code — they are
curated job lists: `Daily-H1B-Jobs-In-Tech` (323 stars),
`2026-Engineer-Internship` (84), `2026-Product-Management-Internship` (141),
and so on.

That is a free distribution channel, and a good one: GitHub-hosted "awesome
list" style job boards accumulate stars, forks and inbound links, rank in Google,
and reach exactly the audience that is job hunting. It costs a scheduled job and
a repo.

Relevance to `launch-plan.md`: the Swedish and UK equivalent does not exist. A
maintained `sweden-tech-jobs` / `uk-graduate-jobs` repo, updated daily from the
hunt that already runs twice a day, is a plausible Tier-2 channel that plays to
infrastructure JobFinderOS already has. **Check the source terms first** — the
JobTech/Platsbanken and Reed licences govern redistribution, and this is exactly
the caching/redistribution question `CLAUDE.md` already flags as open.

## 4. How to re-run this

```bash
# 1. Response headers — CDN, framework, hosting
curl -sSL -o /dev/null -D - https://TARGET | grep -iE '^(server|x-powered-by|x-vercel|via|x-amz)'

# 2. DNS — the richest passive source. TXT records name every SaaS
#    vendor the company has verified a domain with.
dig +short MX TARGET
dig +short TXT TARGET

# 3. Subdomains — the product almost never lives on the apex
for s in app api my dashboard auth admin static assets; do dig +short "$s.TARGET"; done

# 4. Served HTML — framework tells and third-party scripts
curl -sSL https://TARGET | grep -oiE '_next/static|wp-content|__NEXT_DATA__|/_nuxt/'

# 5. Public GitHub org — by far the highest-fidelity source when it exists.
#    Hiring take-homes disclose the production stack deliberately.
curl -sS "https://api.github.com/orgs/ORG/repos?per_page=100"
```

Two more worth adding by hand: **job postings** (engineering ads list the stack
outright) and **certificate transparency** (`crt.sh?q=%25.TARGET&output=json`
enumerates subdomains from published certificates — it was rate-limiting on
2026-08-31, retry later).

`Wappalyzer` and `BuiltWith` automate steps 1–4 if you would rather not.

## 5. The line

Everything above is passive: reading what a public web server volunteers to any
visitor, published DNS, and repositories the companies made public on purpose. A
handful of requests to a homepage is ordinary browsing.

What is **not** on the table, and is not needed: authenticating to their
products to inspect internals, scraping behind a login, enumerating API
endpoints or directories, testing for vulnerabilities, or any volume of traffic
that a reasonable operator would experience as a scan. The line is *reading what
is served* versus *probing to find what is not.* Stay on the reading side — the
useful intelligence is all there anyway, and §2 is the proof of it.
