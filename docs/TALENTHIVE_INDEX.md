# TalentHive — Complete Project Index

> Source: https://github.com/AgenticTony/TalentHiv.git (cloned to `talenthive/`)
> Purpose of this index: full reference of what exists in TalentHive so JobFinderOS can reuse it as a foundation.
> Indexed: 2026-08-24

---

## 1. What TalentHive Is

**TalentHiv** ("AI-Powered Candidate Screening with Dignity") is a recruiter-side tool:

1. Recruiter creates a **Job** (title, company, description, requirements list)
2. Candidates' **CVs are uploaded** (PDF → text extraction via PDFPlumber)
3. **AI screening** scores every CV against the job requirements (GLM via Z.ai, OpenAI-compatible API)
4. Each candidate gets a **tier + score + personalized email draft**

**JobFinderOS inverts this**: the *job seeker* is the user. One CV on file, many jobs flowing in
from scraped job sites. The same matching engine runs in reverse — "should *I* apply to *this* job?"

| | TalentHive (recruiter) | JobFinderOS (job seeker) |
|---|---|---|
| Fixed input | One job description | One CV / profile |
| Streaming input | Many candidate CVs | Many scraped job postings |
| AI question | "Is this candidate a match?" | "Is this job a match for me?" |
| Output | Tiered candidates + rejection/invite emails | Ranked job recommendations + apply queue |
| Action | Send emails | Auto-apply after approval |

---

## 2. Architecture (as deployed)

```
Frontend (Next.js 16 / React 19 / Tailwind 4 / Zustand) → Vercel
Backend  (FastAPI / SQLAlchemy 2.0 / PostgreSQL)         → Azure App Service (Linux)
AI       (Z.ai GLM-4.5, OpenAI-compatible endpoint)      → api.z.ai/api/coding/paas/v4
Storage  (CV PDFs → Azure Blob Storage)
Infra    (Bicep templates: postgres, key-vault, storage, web-app, app-service-plan)
```

---

## 3. File-by-File Index (source only, build artifacts excluded)

### 3.1 Backend — `talenthive/backend/`

| File | Role | Key contents / reuse value for JobFinderOS |
|---|---|---|
| `app/main.py` | FastAPI entry point | CORS middleware, router registration under `/api/v1/*`, `init_db()` on startup, `/health` endpoint, global exception handler. **Reuse pattern directly.** |
| `app/core/config.py` | Pydantic settings | Env vars: `DATABASE_URL`, `GLM_API_KEY`, `RESEND_API_KEY`, `SECRET_KEY`, `AZURE_STORAGE_*`, `CORS_ORIGINS`, rate-limit + email config. **JobFinderOS reuses `GLM_API_KEY` + same Z.ai endpoint + `RESEND_API_KEY` for apply emails.** |
| `app/core/database.py` | SQLAlchemy engine/session | `SessionLocal`, `get_db()` dependency, `init_db()` with `create_all` + ad-hoc column migrations. Pool pre-ping enabled. **Reuse pattern (we default to SQLite locally).** |
| `app/core/security.py` | Auth/security utilities | (basic) |
| `app/core/exceptions.py` | Custom exceptions | |
| `app/models/user.py` | `User` ORM model | |
| `app/models/job.py` | `Job` ORM model | `title, company, location, description, requirements (JSON text), salary_range, employment_type, status (draft/active/closed)`. **Basis for JobFinderOS `JobPosting` (adds source, URL, application_email, scraped_at).** |
| `app/models/candidate.py` | `Candidate` ORM model | `name, email, phone, cv_text, cv_file_path, raw_skills/experience/education (JSON)`. **Basis for JobFinderOS `Profile` (one profile instead of many candidates).** |
| `app/models/screening.py` | `Screening` ORM model | `tier (strong_match/potential_match/not_this_time), score 0-100, reasoning, key_strengths, gaps, transferable_skills, recommendation (proceed/maybe/pass), confidence, email_draft, model_used, processing_time_ms`. **Basis for JobFinderOS `MatchResult`.** |
| `app/models/consent.py`, `email_log.py` | GDPR consent + email audit | |
| `app/schemas/*.py` | Pydantic request/response models | job / candidate / screening DTOs |
| `app/crud/*.py` | DB query layer | jobs, candidates, screenings, consent, email_logs, users |
| `app/api/v1/jobs.py` | Job CRUD | `POST/GET /jobs`, `GET/DELETE /jobs/{id}`, `JobWithStats` |
| `app/api/v1/candidates.py` | Candidate mgmt | `POST /jobs/{id}/candidates` (text), `POST /jobs/{id}/candidates/upload` (PDF), list, delete |
| `app/api/v1/screenings.py` | Screening results | create + list endpoints |
| `app/api/v1/demo.py` | Demo orchestration | `POST /demo/load`, `/demo/screen/{job_id}` (loops candidates → AI), `/demo/full`, `/demo/quick`. **This loop pattern becomes JobFinderOS's pipeline: scrape → match → recommend.** |
| `app/api/v1/compliance.py` | GDPR endpoints | privacy policy, consent give/withdraw, data export, deletion, retention info |
| `app/api/v1/admin.py` | Admin | retention cleanup tasks |
| `app/services/ai_service.py` | ⭐ **THE CORE — AI screening** | See §4 below. **Directly adapted into JobFinderOS matcher.** |
| `app/services/file_service.py` | PDF handling | `extract_text_from_pdf()` (pdfplumber), `validate_pdf()` (size + `%PDF` header). **Reused verbatim.** |
| `app/services/retention_service.py` | Data retention policies | |
| `requirements.txt` | Pins | fastapi 0.68, sqlalchemy 2.0.25, openai 1.12, pdfplumber 0.10.3, resend 2.0.0, pydantic 1.10 (JobFinderOS uses pydantic v2 on Python 3.13) |
| `tests/` | Pytest suite | API + CRUD + service tests with conftest |

### 3.2 Frontend — `talenthive/frontend/`

| File | Role | Reuse value |
|---|---|---|
| `src/app/page.tsx` | Landing page | marketing page |
| `src/app/dashboard/page.tsx` | Screening dashboard | **Pattern basis for JobFinderOS dashboard** |
| `src/app/api/cron/route.ts` | Vercel cron hook | |
| `src/components/BulkUpload.tsx` | Multi-file CV upload w/ progress | **Basis for CV upload in Profile page** |
| `src/components/FileUpload.tsx` | Single file upload + preview | reused pattern |
| `src/components/CandidateCard.tsx` | Expandable result card | **Basis for job match card** |
| `src/components/ScoreRing.tsx` | Animated score ring | **reused for match scores** |
| `src/components/TierBadge.tsx` | Tier color badge | reused for match tiers |
| `src/components/JobSelector.tsx`, `JobCreateModal.tsx` | Job CRUD UI | |
| `src/components/EmailPreview.tsx` | Draft email preview | **basis for cover-note preview before apply** |
| `src/components/FilterBar.tsx`, `StatCard.tsx`, `TopNav.tsx`, `Toast.tsx`, `PrivacyBanner.tsx`, `FeedbackSection.tsx` | UI kit | reused patterns |
| `src/lib/api.ts` | Axios API client, typed | **pattern reused; long-running client (10 min timeout) kept for AI pipeline calls** |
| `src/lib/utils.ts` | helpers | |
| `src/store/candidates.ts` | Zustand store | pattern reused |
| `src/types/index.ts` | TS types | extended for JobFinderOS |
| `package.json` | next 16.2, react 19, tailwind 4.2, zustand 5, framer-motion 12, axios, lucide-react | **same stack reused** |

### 3.3 Infrastructure — `talenthive/infrastructure/`

Azure Bicep: `main.bicep` + modules (postgres, key-vault + access, storage, app-service-plan, web-app). `azure.yaml` (AZD config), `scripts/deploy-infra.sh`, `vercel.json` (frontend deploy). JobFinderOS v1 runs locally / single service — infra deferred.

---

## 4. The Core Asset: `ai_service.py` — How the Matching Works

**Client setup (reused identically in JobFinderOS):**
```python
OpenAI(api_key=GLM_API_KEY, base_url="https://api.z.ai/api/coding/paas/v4")
model = os.getenv("GLM_MODEL", "glm-4.5")   # glm-4.5 fast (2–3s); glm-5 slow thinking model
temperature=0.3, max_tokens=2000
```

**Structured 5-step screening prompt** (system prompt, enforced order):
1. **EXTRACT** facts from CV (years exp, tech, titles, industries, education, certs, achievements) — with a *language rule*: translate everything to English internally so CVs in Swedish/French/etc. score identically.
2. **SCORE per requirement**: +12 fully met · +6 partial/transferable · 0 absent · −8 critical missing. Sum = raw score.
3. **TIER from score** (strict): 80–100 `strong_match` · 50–79 `potential_match` · 0–49 `not_this_time`.
4. **TRANSFERABLE SKILLS**: actively map cross-industry experience.
5. **EMAIL DRAFT** per tier, <150 words.

Plus **bias rules** (ignore name, gender, age, nationality, CV language, university prestige, employment gaps) and a **consistency check** (score must match tier, strengths must be evidenced).

**Response contract (JSON):**
```json
{
  "tier": "strong_match|potential_match|not_this_time",
  "score": 0-100,
  "reasoning": "...",
  "key_strengths": [], "gaps": [], "transferable_skills": [],
  "recommendation": "proceed|maybe|pass",
  "confidence": "high|medium|low",
  "email_draft": {"subject","greeting","body","closing"}
}
```

Robustness handling worth keeping: markdown-fence JSON extraction, GLM-5 `reasoning_content` fallback, default result on error, 90s httpx timeout.

---

## 5. What JobFinderOS Reuses vs. Builds New

**Reused from TalentHive (concept and/or code):**
- GLM AI client config (same `GLM_API_KEY`, same Z.ai base URL, glm-4.5 default) → existing key works
- Structured per-requirement scoring approach + tier system (retuned for job-seeker direction)
- Transferable-skills step and bias-mitigated prompting
- PDF extraction service (pdfplumber) — reused verbatim
- FastAPI layout: `api/v1` routers, `crud`, `models`, `schemas`, `services`, `core`
- Frontend stack: Next.js + Tailwind + Zustand + axios + ScoreRing/TierBadge-style components
- Resend for outbound email (was recruiter emails → becomes application emails)

**New in JobFinderOS:**
- `Profile` — one CV on file, AI-extracted into a structured seeker profile
- Scrapers — Arbeitnow, Remotive, Jobicy, Working Nomads (free public APIs, pluggable base class)
- `MatchResult` — job-seeker-direction scoring with apply recommendation + tailored cover note
- Approval workflow — recommended → approved/rejected → apply queue
- Apply service — email apply (Resend/SMTP), manual/browser queue for portal applications
- Scheduler — optional background scrape→match loop
- SQLite by default (zero-config local run), Postgres via `DATABASE_URL`

---

## 6. Running TalentHive Locally (for reference)

```bash
cd talenthive/backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set DATABASE_URL, GLM_API_KEY
uvicorn app.main:app --reload --port 8000

cd ../frontend && npm install && npm run dev
```
API docs at `http://localhost:8000/docs`. Note: pins (pydantic 1.10) predate Python 3.13; JobFinderOS backend is pydantic-v2 and runs on 3.13.
