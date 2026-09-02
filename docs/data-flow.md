# JobFinderOS — Data Flow & Subprocessor Map

**Purpose.** Internal, factual record of where candidate data travels —
the evidence base for any future EU-processing claim. Written
2026-09-01 against the live architecture. Update this file whenever a
processor, region, or flow changes; the marketing claim in the last
section may only be used while this document supports it.

**Status legend:** ✅ in place · 🔄 planned (tracked) · 🔍 needs
verification · ❌ open gap

## The claim ladder (what we may say, and when)

| Stage | What we can honestly say |
|---|---|
| **Today** | CV file and text are **stored** in the EU; AI matching and tailoring are processed **outside the EU** (disclosed in the privacy notice §5). |
| **After MIG-WO5 + backup verification** | "Your CV file and text are stored and processed in the EU — Ireland and Frankfurt — and AI matching runs on EU-hosted models." |

Never use absolute forms ("nothing ever leaves Europe"). A subprocessor
change at any provider would falsify an absolute claim; the precise
wording above is verifiable and defensible.

## Candidate-data flow (end to end)

```
CV upload (browser)
  └─> HTTPS POST → FastAPI on Render (Frankfurt, eu-central)
        ├─ CV file (PDF or Word .docx) stored → Supabase Storage (eu-west-1, Ireland)
        ├─ extracted text + profile → Supabase Postgres (eu-west-1)
        ├─ matching / tailoring / fact-guard → AI inference:
        │      TODAY:  Z.ai api.z.ai            — OUTSIDE EU ❌
        │      PLAN:   Mistral EU endpoint      — MIG-WO5 🔄
        └─ results (scores, drafts) → Supabase Postgres (eu-west-1)

Dashboard reads ← HTTPS ← Supabase eu-west-1 via Render Frankfurt.

Nightly backup (04:30 local): pg_dump + Storage export →
client-side encrypted (rclone crypt) → Backblaze B2 — region 🔍
(verify eu-central; move bucket if US).

Feedback page → row in Supabase (eu-west-1) + owner notification
via Resend (US) — carries account email + feedback text, never CV
content. Decide: EU SMTP route or disclose. 🔍
```

Flows that never touch candidate data:
- **Job scraping** (Arbetsförmedlingen/JobTech, Reed, Careerjet,
  Adzuna, Remotive) — public ad data only, outbound from Render.
- **Analytics (GA4, consent-gated via Cookiebot)** — page/event
  measurement only; no CV content; loads only after statistics consent.
- **Cloudflare Pages** — static assets only; the JWT lives in the
  visitor's localStorage; API calls go directly to Render Frankfurt.
- **Sentry** — disabled (no DSN configured).

## Subprocessor / transfer register

| Processor | Purpose | Region | Candidate CV data? | Status |
|---|---|---|---|---|
| Supabase | DB + Storage (+ Auth after MIG-WO2) | eu-west-1 Ireland | Yes — stored | ✅ |
| Render | API + hunt worker | Frankfurt | Yes — processed | ✅ |
| Z.ai | AI match/tailor/judge inference | Outside EU | Yes — CV text | ❌ → MIG-WO5 |
| Mistral | Same, EU-hosted inference | EU (verify + DPA) | Yes — CV text | 🔄 MIG-WO5 |
| Backblaze B2 | Encrypted off-site backups | Bucket region? | Yes — encrypted dumps | 🔍 verify eu-central |
| Resend | Owner feedback notifications | US | No — feedback text + email only | 🔍 decide |
| Google (GA4) | Site analytics, consent-gated | US (measurement only) | No | ✅ disclosed |
| Cookiebot | Consent management | EU (Cybot, DK) | No | ✅ |
| Cloudflare | Static hosting / CDN | Edge (no personal data) | No | ✅ |
| Google (user's Gmail) | Applications, post-beta | User's own account | Sent BY the user from their own mailbox (first-party OAuth) | 🔄 planned |
| OpenAI/none other | — | — | — | n/a |

## Checklist to earn the EU-processing claim

1. **MIG-WO5** — AI inference switched to the Mistral EU endpoint
   (config already armed; no code reads MISTRAL_API_KEY yet).
2. **Mistral DPA** signed; confirm the inference region in writing.
3. **Backups** — confirm the B2 bucket region in the Backblaze
   dashboard; if US, create an eu-central bucket and repoint
   `jfos-b2-crypt` (re-encrypt or dual-write during migration).
4. **Feedback email** — either route owner notifications through an
   EU SMTP provider or add the transfer to the privacy notice (does
   not carry CVs; not strictly a blocker for the CV claim).
5. Flip the wording in the privacy notice (§5 beta note) and only then
   surface "EU data processing" on the landing trust line.

## Positioning note (owner + advisor decision, 2026-09-01)

EU processing is the **trust layer**, not the proposition. Hierarchy:
1. Primary: *find the jobs you actually have a chance of getting*
2. Underneath: *evidence-based matching · EU data processing · nothing
   invented*

The B2B variants (recruitment firms, universities, outplacement) quote
the architecture above — this document is what we hand a procurement
or security reviewer, not marketing copy.
