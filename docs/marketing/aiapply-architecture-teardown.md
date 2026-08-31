# AIApply — architecture teardown and cost model

> Built 2026-08-31 from AIApply's own published hiring take-home
> (`github.com/aiapply/auto-apply-job-matching-assignment`, public) plus the
> passive fingerprinting in `competitor-tech-stacks.md`. Corpus statistics are
> **measured**, not estimated: their 50,000-posting sample was downloaded and
> parsed. Everything marked *modelled* is arithmetic on stated inputs, flagged
> as such.
>
> **What this is not:** their production code. A take-home is a simplified
> version of the real system, published to be solved by candidates. It is
> strong evidence of how they frame the problem and which tools they hand
> people, not a guarantee of what runs in production.

## 1. The stack, as they disclose it

From the repo's `.env.example` — their own dev tenant, committed:

```
AZURE_OPENAI_ENDPOINT=https://aiapply-dev.services.ai.azure.com/
AZURE_OPENAI_API_VERSION=2025-01-01-preview
#   gpt-5.4-nano            chat
#   text-embedding-3-small  embeddings
```

From `docker-compose.yml`: `getmeili/meilisearch:latest`.
From `requirements.txt`: `meilisearch==0.37.0`, the only pinned dependency.
From the sibling `laravel-assessment` repo: Laravel 12 + Sanctum, Vue 3,
Tailwind 4, MySQL 8, Docker.

So the full picture:

| layer | technology | evidence |
|---|---|---|
| Application | Laravel 12 + Sanctum, Vue 3, Tailwind 4, MySQL 8 | `laravel-assessment` README |
| Matching pipeline | **Python 3.12** | assignment README |
| Search / retrieval | **Meilisearch** (hybrid: BM25 + vectors) | `docker-compose.yml`, pinned dep |
| Embeddings | **`text-embedding-3-small`** | `.env.example` |
| Chat / rerank | **`gpt-5.4-nano`** | `.env.example` |
| Inference vendor | **Azure OpenAI**, not OpenAI direct | endpoint host |
| CDN / infra | Cloudflare; Squarespace marketing site | `competitor-tech-stacks.md` |
| Analytics | PostHog **EU** cloud | homepage |

**Azure OpenAI rather than OpenAI direct** is the notable one. It implies an
enterprise Azure commitment, gives them EU/regional data residency (consistent
with PostHog EU), and means their inference is on committed capacity — which
matters, because it makes their per-call cost lower and more predictable than
list API pricing suggests.

## 2. The pipeline they ask candidates to build

Required entry points, verbatim from the README:

```bash
python ingest.py                 # load the postings into your index
python search.py --profile p1    # write results.json
```

Profile in → ranked shortlist of **~10** out. Stated constraint: *"in production
this corpus is over a million postings and grows every hour. The approach that
works on 50,000 is not always the one that works on 1,000,000."*

Given the two model deployments handed to candidates, the intended shape is
almost certainly **hybrid retrieval, then narrow rerank**:

1. **Ingest** — normalise, embed title+description with `text-embedding-3-small`,
   store the vector alongside BM25-indexed text in Meilisearch.
2. **Filter** — hard structured predicates: `can_legal_work`, `need_visa_sponsorship`,
   `experience_level`, `work_type`, and the separated location scopes.
3. **Retrieve** — hybrid query (semantic + keyword) for a candidate set.
4. **Rerank** — optionally `gpt-5.4-nano` over the top *N*, not the corpus.
5. **Emit** — top 10 to `results.json`.

### The correction this forces on our earlier read

An earlier note in this repo said their pipeline has *"no LLM call."* That was
based on `requirements.txt` alone and is **wrong**. Precisely:

- There **is** an LLM/embedding cost **per job, once, at ingest** — then reused
  by every user forever.
- There is **no per-(job × user) LLM call at query time**.

That distinction is the entire economic story, and §4 quantifies it.

## 3. The corpus — measured, not guessed

Parsed from their published `jobs.jsonl.gz` (73 MB compressed):

| metric | value |
|---|---|
| postings | 50,000 |
| raw JSONL | **279.9 MB** (5,598 B/posting) |
| description text | 244.7 MB — mean **4,893** chars, median 4,598, p90 7,906 |
| `is_remote` true | 3,744 (**7.5%**) |
| has any salary | 30,736 (**61.5%**) |
| multi-location postings | 4,453 (8.9%) |
| zero-location postings | 0 |

### The finding that matters most to us

Location entries by country code:

| US | CA | GB | MX | ES | AU | DE | IN | **SE** |
|---|---|---|---|---|---|---|---|---|
| 57,320 | 5,060 | **133** | 55 | 42 | 40 | 40 | 35 | **0** |

**Their corpus is ~90% United States and Canada. It contains 133 UK location
entries and zero Swedish ones.**

In our two markets, AIApply is not a competitor — it is a US product with a
rounding error of UK coverage and no Swedish presence at all. This is the
sharpest strategic fact in this document, and it reframes
`competitive-positioning.md`: we compete with them on *positioning and
reputation* (their billing complaints shape our pricing promises), not on
*coverage*. In Sweden nobody is competing with us on data.

Two caveats: this is a sample published for a take-home, so it may be
deliberately trimmed or US-skewed for the exercise; and their production corpus
is stated as 20× larger. But a corpus that is 90% US at 50k is unlikely to be
majority-European at 1M.

### Their data problems are the ones we already solved

`data/README.md` warns candidates about, in their words:

- *"`company_name` … may be an agency rather than the employer"* — our
  fuzzy agency/direct dedupe gate.
- *"often long, sometimes not in English"* — our language gate.
- *"Onsite scope and remote scope are separate … collapsing them into one
  location filter will cost you"* — our PIPE-16 scope gate, which exists
  because of a live incident with exactly that shape.
- *"Nothing here is normalised for you."*

They are hiring to solve problems `matcher_service.py` already handles. Also
worth noting: their README says `salary_min` is *"usually null"*, but 61.5% of
the sample carries at least one salary field — their own docs are stale about
their own data.

## 4. The cost model

### 4a. Meilisearch Cloud pricing (verified 2026-08-31)

| model | price | what it buys |
|---|---|---|
| Resource-based, XS | **$23/mo** — $18 instance + $5 disk | 0.5 vCPU, 1 GB RAM, 32 GiB disk |
| Resource-based, tiers | XS → 4XL self-serve, larger via Sales; **dedicated from ~$144/mo** | dedicated CPU/RAM/storage |
| Usage-based | **$30/mo** base | 100K documents, 50K searches; overages billed, rates unpublished |
| Free trial | 14 days, no card | — |
| Self-hosted | **$0** licence | you run it |

Meilisearch's own guidance is that resource-based suits *"high-traffic or vector
workloads"* — which is AIApply's case. Per-unit overage rates are not published,
so a usage-based projection at their scale cannot be computed honestly.

### 4b. What 1M postings actually costs — *modelled*

Inputs: measured 5,598 B/posting; mean description 4,893 chars ≈ **1,223 tokens**
(chars ÷ 4); `text-embedding-3-small` at **$0.02 / 1M tokens**, 1536 dimensions.

| line | 50k (measured sample) | 1M (their stated scale) |
|---|---|---|
| raw documents | 280 MB | **5.6 GB** |
| embedding tokens | 62.5M | 1.25B |
| **embedding cost, one-off** | **$1.25** | **~$25** |
| vectors, float32 (1536 × 4 B) | 307 MB | **6.1 GB** |
| vectors, binary-quantized (1536 bits) | 9.6 MB | **192 MB** |
| Meilisearch instance | XS, $23/mo | **mid/large tier — modelled $300–800/mo** |

Two things follow:

- **Embedding a million job postings costs about $25.** It is not the expense.
  Binary quantization — which Meilisearch supports — takes the vector footprint
  from 6.1 GB to 192 MB, a 32× reduction, and is what makes a corpus this size
  fit in a sane instance.
- **The recurring cost is the instance, not the AI.** The $300–800/mo band is
  *modelled* by extrapolating from the published XS tier and the ~$144 dedicated
  floor; Meilisearch does not publish the intermediate tier table, so treat it as
  an order of magnitude, not a quote.

Churn: postings expire in roughly 30–60 days, so a 1M steady-state corpus turns
over on the order of 1M/month — another **~$25/month** in embeddings.

### 4c. Marginal cost per user — the actual asymmetry

> ### ⚠ Correction — 2026-08-31, from live telemetry
>
> **The figures below were modelled before `ai_usage` data existed. Measurement
> supersedes them.** 518 real calls (Supabase Postgres, 28–31 Aug) say:
>
> | measured | value |
> |---|---|
> | match calls | 507 |
> | **prefix cache hit rate** | **86.2%** — Z.ai caches the rubric+CV prefix automatically |
> | avg input / cached / output | 3,705 / 3,193 / 336 tokens |
> | **cost per match call** | **$0.00302** |
> | calls per job (sampling protocol) | 1.64 |
> | total match spend | $1.53 |
>
> Two things this changes:
>
> 1. **Output tokens are now 49% of match spend.** After caching, 336 output
>    tokens cost more than all 3,705 input tokens. Any further optimisation
>    targets output, not input — the "stop resending the CV" idea was already
>    won automatically before it was proposed.
> 2. **The right shape is a drain curve, not a monthly per-user rate.** That
>    $1.53 was one user draining an accumulated pool over 2.5 days.
>    `unique(user_id, job_id)` plus watermarks mean it never repeats: steady
>    state is delta-inflow only. Quote the curve — first-week drain, then far
>    below it — not a flat monthly figure.
>
> **What survives:** the *structural* asymmetry, which is what this document is
> actually about. They pay inference **once per job, ever, amortised across all
> users**; we pay **once per (job × user)**. That inversion is real and
> permanent. The "90×" multiple below was arithmetic on a modelled €3.36/month
> against a modelled $0.05/month, and both sides were estimates — treat the
> direction as established and the multiple as retired.

**AIApply:** the expensive work (embedding) happens **once per job** and is
amortised across every user who ever searches. A query costs one profile
embedding (~$0.00001) plus an optional `gpt-5.4-nano` rerank over the top *N*
candidates — *N* being the design choice the take-home is really testing.

**JobFinderOS:** `glm-5.1` scores every job that survives the cheap gates, per
user. **Measured: $0.00302 per match call, 1.64 calls per job** — so roughly
half a cent per job per user, against effectively zero marginal cost for them.

The asymmetry is structural — a consequence of *per-job* versus
*per-(job × user)* inference — and it will not close by shortening prompts.
It is also the thing we are buying the verdict with.

## 5. What this means for us

**Do not read the cost asymmetry as a defect to be fixed.** It is the price of a different
product. Their pipeline returns a *ranked list*; ours returns a *reasoned
verdict* — skills held, gaps demanded, what transfers — which is what the
landing page sells and what a ranking architecture structurally cannot produce.
Their matches cannot explain themselves because nothing ever read the job.

Three concrete conclusions:

1. **The cheap gates are the most valuable code in the repo.** Language → scope →
   cross-board dedupe → fuzzy agency dedupe is what stops LLM spend scaling with
   corpus size. Every hour spent there buys more than prompt tuning ever will.
2. **A retrieval layer is the proven scaling path, and it is additive, not a
   rewrite.** If the pool outgrows Postgres predicates at the gate stage,
   hybrid retrieval in front of scoring narrows the candidate set cheaply so the
   expensive verdict runs on fewer, better jobs. Self-hosted Meilisearch is $0 in
   licence and runs in a container. **Not now** — at ~385 rows it would be
   architecture astronautics — but it is the right shape when the pool justifies
   it, and it is worth knowing the incumbent already lives there.
3. **Coverage, not cleverness, is our moat in SE/UK.** Their corpus has zero
   Swedish postings. Platsbanken's national feed is the whole Swedish market and
   it is open data. No amount of Meilisearch tuning gets them Swedish jobs they
   have not ingested — and a US company will not prioritise a market this size.

## 5b. The two pipelines, side by side

> Their column is the take-home's intended shape (the pieces are evidenced —
> Meilisearch, `text-embedding-3-small`, `gpt-5.4-nano`, the required entry
> points — the ordering is inferred). Our column is traced from code:
> `pipeline.py`, `matcher_service.py`, `draft_service.py`.

### AIApply

```
INGEST — once per job, shared by every user, forever
  1  aggregate into the corpus (1M+, "grows every hour")
  2  normalise                      [unsolved — this is the take-home]
  3  embed title+description        text-embedding-3-small
  4  index                          Meilisearch: BM25 text + vector

QUERY — per user, per search
  5  load profile                   titles, onsite/remote split, level,
                                    work_type, salary, visa, can_legal_work
  6  hard filters                   Meilisearch filterable attributes
  7  embed profile → hybrid query   semantic + keyword
  8  candidate set                  top N
  9  rerank (optional)              gpt-5.4-nano over top N only
 10  emit                           top 10 → results.json

APPLY — the actual product, absent from the take-home
 11  auto-apply via browser automation        metered as credit packs
 12  document generation per application
```

### JobFinderOS

```
INGEST — shared pool, union-scoped across all users
  1  build_union_contexts           every user's queries+municipalities unioned,
                                    plus per-anchor radius and region contexts
  2  delta_since_for                watermark incremental — jobtech ONLY
                                    (DELTA_SOURCES = {"jobtech"}); other 7 full-fetch
  3  scrape_source × 8              country-routed via _select_sources
  4  ingest gate                    passes_radius_gate | passes_location_filter
  5  _job_exists                    dedupe_key
  6  _insert_job_posting → shared job_postings
  7  set_watermarks

MATCH — per user, the expensive half
  8  select unmatched               outerjoin: no match row for THIS user
  9  _apply_cheap_gates             ← the whole cost story lives here
       a  language gate
       b  PIPE-16 scope gate        stored_job_in_user_scope → out_of_scope
       c  cross-board dedupe        dedupe_key already matched
       d  _dismiss_fuzzy_duplicates agency vs direct
 10  loop, bounded by limit + max_seconds
       excluded_keyword / no_description → dismiss, no AI spent
       service.match_job()          glm-5.1, temp 0, anchored rubric
       needs_another_sample()       dead-band [13,25) → 2nd sample
       resolve_samples()            mean
       < KEEP_MIN(25) → dismiss below_threshold
       else → MatchResult + prompt_version + tier + skills

USER
 11  approve the match

DRAFT
 12  tailor CV + cover letter for THAT job
 13  Layer A  unsupported_claims()   deterministic, translation-invariant
 14  Layer B  judge_fabrication()    LLM judge
 15  Layer C  high-conf → retry → block; advisory → review UI
 16  user edits, approves

SEND
 17  email + 3 PDFs (Resend), or browser/manual
```

### Where they actually differ

| | AIApply | JobFinderOS |
|---|---|---|
| **when inference happens** | **ingest** — once per job | **match** — per (job × user) |
| **does anything read the job?** | no, at query time | yes, every kept job, per user |
| **output** | ranked list of 10 | reasoned verdict: have / want / transfers |
| **filtering** | query-time attributes | 4 cheap gates *before* any spend |
| **dedupe** | candidate's problem, unsolved | `dedupe_key` + fuzzy agency/direct |
| **language** | candidate's problem | language gate |
| **location** | they warn onsite≠remote | scope gate mirrored ingest↔match, + radius |
| **incremental fetch** | not addressed | watermarks — **jobtech only** |
| **multi-tenant fetch** | corpus is shared by nature | `build_union_contexts` |
| **fabrication** | **absent — not even framed as a problem** | 3-layer guard, blocks the send |
| **cost driver** | corpus size — **fixed** | corpus × users — **variable, uncapped** |
| **proven at** | 1M+ postings | ~385 |

Three readings worth holding:

1. **They front-load the expensive thinking; we back-load it.** Embedding once
   per job buys scale. Scoring per (job × user) buys judgement. Neither is
   a mistake — but it is why their matches cannot explain themselves and ours
   cost roughly half a cent per job per user (measured), against effectively
   zero marginal cost for them.
2. **Their take-home problem is our solved problem.** Normalisation, agency
   dedupe, language, the onsite/remote split — they are hiring to build what
   `matcher_service.py` already does, hardened by live incidents.
3. **Our solved problem is not even on their map.** A job-matching assignment
   with no notion of checking generated output against the source CV. That gap
   is the whole differentiation, and it is the one thing the market has started
   punishing.

**The honest weak spot in our column:** `DELTA_SOURCES = {"jobtech"}`. Seven of
eight sources re-fetch in full every run. That is fine at this pool size and
becomes the first thing to fix when it is not.

## 6. Method

Everything here came from: a public GitHub repository the company published for
job candidates, a dataset they attached to it, and their own pricing page.
No authentication, no scraping behind a login, no probing. The 50k dataset was
downloaded once and parsed locally.

```bash
curl -sS "https://api.github.com/repos/aiapply/auto-apply-job-matching-assignment/git/trees/main?recursive=1"
curl -sSL -o jobs.jsonl.gz "https://raw.githubusercontent.com/aiapply/auto-apply-job-matching-assignment/main/data/jobs.jsonl.gz"
```

Sources: [Meilisearch pricing](https://www.meilisearch.com/pricing) ·
[resource-based pricing announcement](https://www.meilisearch.com/blog/resource-based-pricing) ·
[binary quantization](https://github.com/Kerollmops/blog/issues/16) ·
[OpenAI embedder config](https://www.meilisearch.com/docs/capabilities/hybrid_search/how_to/configure_openai_embedder)
