# JobFinderOS

**An operating system for job hunting — scrape · match · approve · apply.**

JobFinderOS inverts [TalentHive](https://github.com/AgenticTony/TalentHiv) (the recruiter-side
AI screening tool): instead of many CVs screened against one job, **your one CV** is matched
against **many jobs** scraped from job sites — then, after you approve the recommendations,
it applies for you.

```
   TalentHive (recruiter)              JobFinderOS (you)
   1 job  + 100 CVs   ──AI──▶ tiers    1 CV + 100 scraped jobs ──AI──▶ ranked
                                   │                                        │
                                   └── emails candidates                   └── you approve → auto-apply
```

---

## The Pipeline

```
┌────────┐   ┌────────┐   ┌────────────┐   ┌──────────────┐   ┌───────────────┐   ┌─────────────┐
│ SCRAPE │──▶│ MATCH  │──▶│  APPROVE   │──▶│    TAILOR    │──▶│    REVIEW     │──▶│    SEND     │
│ 9 sources │   │ vs CV  │   │  (you)     │   │ CV+cover foe │   │ edit if needed│   │ email/browser│
└────────┘   └────────┘   └────────────┘   │  this job    │   │  (you)        │   │ + PDFs      │
                                           └──────────────┘   └───────────────┘   └─────────────┘
```

1. **Scrape** — free public job APIs (no keys needed): JobTech/Platsbanken (SE), Reed + Adzuna + Careerjet (GB), Teamtailor, Arbeitnow, Remotive, Jobicy, Working Nomads
2. **Match** — every new job is scored against your CV (0-100) with tier, matched/missing/transferable
   skills and an *apply / maybe / skip* recommendation (talks directly to you: "Your tech stack…")
3. **Approve** — you approve/reject each recommendation (nothing happens without you)
4. **Tailor** — approving triggers AI tailoring of your CV + cover letter for that specific job,
   with a "what changed and why" summary addressed to you
5. **Review** — read and edit both documents in the Applications tab; nothing is sent without
   your final approval
6. **Send** — email applies send the tailored cover letter + tailored CV (PDF) + your original CV
   via Resend; portal applies open in your browser with the cover letter one click away

### Match tiers (adapted from TalentHive's scoring system)

| Tier | Score | Meaning |
|------|-------|---------|
| Excellent Match | 80-100 | Meets 80%+ of requirements with evidence — apply now |
| Good Match | 50-79 | Core requirements met or strong transferables — worth applying |
| Stretch | 30-49 | Notable gaps — a growth bet |
| Poor Match | 0-29 | Fails core requirements — skip |

---

## Quick Start

### 1. Backend (FastAPI, Python 3.13)

```bash
cd backend
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt   # or: pip install -r requirements.txt

cp .env.example .env
# >>> put your TalentHive GLM key in .env: GLM_API_KEY=... <<<

uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 2. Frontend (Next.js 16 + React 19 + Tailwind 4)

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Dashboard: http://localhost:3000

### 3. Use it

1. **Profile tab** → drop your CV (PDF) — AI extracts your structured profile
2. Click **Run Pipeline** — scrapes all sources + AI-matches new jobs against your CV
3. **Matches tab** → review recommendations → **Approve** the good ones
4. Approved → **Auto-apply by email** (where the job published an email) or **Apply in browser**
5. **Applications tab** → track sent / pending / failed; retry failures

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `GLM_API_KEY` | **yes** (for AI) | Your Z.ai key — **the same one TalentHive uses** |
| `GLM_MODEL` | no | Default `glm-4.5` (fast). `glm-5` works but is slow |
| `DATABASE_URL` | no | Default SQLite (`./jobfinderos.db`). Postgres URL for production |
| `RESEND_API_KEY` | no | Enables email auto-apply (same provider TalentHive used) |
| `APPLY_FROM_EMAIL` | no | Verified Resend sender for applications |
| `SCRAPE_SOURCES` | no | Comma list; default all four |
| `ENABLE_SCHEDULER` | no | `true` = auto-run pipeline every `SCRAPE_INTERVAL_MINUTES` |
| `MAX_JOBS_PER_MATCH_RUN` | no | AI calls per pipeline run (default 25) |

### Frontend (`frontend/.env.local`)

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | Backend URL (default `http://localhost:8000`) |

---

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/profile/upload` | Upload CV PDF → text extraction + AI profile |
| `GET/PUT` | `/api/v1/profile/me` | Get / update profile & preferences |
| `GET` | `/api/v1/profile/status` | Readiness (profile? AI key? stats) |
| `POST` | `/api/v1/pipeline/run` | **The main button**: scrape + match + top recommendations |
| `GET` | `/api/v1/pipeline/status` | Stats + recent scrape runs |
| `GET` | `/api/v1/jobs/` | Scraped jobs (filter by status/source/query) |
| `POST` | `/api/v1/jobs/` | Add a job manually |
| `GET` | `/api/v1/matches/` | AI match results (filter tier/score/pending) |
| `POST` | `/api/v1/matches/{id}/decision` | **Approve / reject** a recommendation |
| `POST` | `/api/v1/matches/run` | Match-only run (background) |
| `POST` | `/api/v1/applications/draft/{job_id}` | **Tailor CV + cover letter** for an approved job |
| `GET` | `/api/v1/applications/drafts` | List application drafts |
| `PUT` | `/api/v1/applications/draft/{id}` | Save your edits to a draft |
| `POST` | `/api/v1/applications/draft/{id}/submit` | **Send** the reviewed package (email / browser) |
| `POST` | `/api/v1/applications/{id}/retry` | Retry a failed email application |

---

## Project Structure

```
JobFinderOS/
├── docs/
│   └── TALENTHIVE_INDEX.md      # ⭐ complete index of the TalentHive foundation
├── talenthive/                  # cloned reference repo (read-only foundation)
├── backend/                     # FastAPI app
│   ├── app/
│   │   ├── api/v1/              # profile, pipeline, jobs, matches, applications
│   │   ├── core/                # config (pydantic-settings), database (SQLite default)
│   │   ├── models/              # Profile, JobPosting, MatchResult, Application, ScrapeRun
│   │   ├── schemas/             # pydantic v2 DTOs
│   │   ├── crud/                # queries + stats
│   │   └── services/
│   │       ├── ai_service.py    # ⭐ GLM matcher — TalentHive's engine, inverted
│   │       ├── cv_service.py    # CV upload → extraction → AI profile
│   │       ├── file_service.py  # pdfplumber (reused from TalentHive)
│   │       ├── matcher_service.py  # match loop (TalentHive demo loop, inverted)
│   │       ├── apply_service.py # email (Resend) / browser / manual apply
│   │       ├── pipeline.py      # scrape → match orchestration
│   │       ├── scheduler.py     # optional APScheduler loop
│   │       └── scrapers/        # base + arbeitnow, remotive, jobicy, workingnomads
│   └── tests/test_flow.py       # mocked end-to-end flow test
└── frontend/                    # Next.js dashboard (TalentHive's stack)
    └── src/                     # app page, components (MatchCard, ScoreRing, CvUpload…)
```

---

## Adding a New Job Source

```python
# backend/app/services/scrapers/mysource.py
class MySourceScraper(BaseScraper):
    source = "mysource"
    def fetch(self) -> list[NormalizedJob]: ...

# register in scrapers/__init__.py, add to SCRAPE_SOURCES env — done.
```

## Notes on Auto-Apply & Terms of Service

Email auto-apply only fires for jobs that **publish an application email** — a normal,
human-equivalent application. Portal applications (LinkedIn Easy Apply, Indeed forms, etc.)
prohibit bot submissions in their ToS, so JobFinderOS keeps a **human in the loop**: it opens
the posting with your tailored cover note ready, and records the application when you mark it done.

## Testing

```bash
cd backend
.venv/bin/python -m tests.test_flow     # mocked-AI end-to-end: match → approve → apply
cd ../frontend
npx tsc --noEmit                        # type check
```

## What's Reused from TalentHive

- GLM client setup (`GLM_API_KEY`, Z.ai endpoint, glm-4.5) — existing key works unchanged
- Structured per-requirement scoring (+12/+6/0/−8), tiers, transferable-skills step, bias rules
- Robust GLM response handling (markdown fences, reasoning_content fallback, error defaults)
- pdfplumber CV extraction (verbatim), FastAPI layering, Resend for email, frontend stack

See **`docs/TALENTHIVE_INDEX.md`** for the complete file-by-file index of the foundation.

---

**Author:** Anthony Foran — built on [TalentHive](https://github.com/AgenticTony/TalentHiv)
