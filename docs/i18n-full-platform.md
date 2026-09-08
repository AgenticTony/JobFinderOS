# Full-Platform Dual Language (EN/SV) — Execution Plan

**Created:** 2026-09-02 · **Owner decision:** the whole platform goes
bilingual, not just the marketing surface.
**Purpose of this doc:** the single source of truth for this effort.
Each phase has exact tasks with checkboxes — update them as work
lands so any session can resume precisely where we left off.

**Already bilingual (PR #57 — do not redo):** landing + login
(`/sv`, `/sv/login`), browser-language detection with stored
preference (`jfos-lang`, `src/i18n/locale.ts`), EN/SV toggle, the
typed dictionary pattern (`src/i18n/dict.ts` — `sv: typeof en` makes
a missing key a compile error), Cookiebot banner (self-localizing).

**Effort estimate:** ~3–4 focused days total across all phases.
Phases are additive — each ships value on its own and the platform
never breaks mid-rollout.

---

## Status tracker (update per session)

| Phase | Scope | State |
|---|---|---|
| 1 | Locale plumbing + console chrome | ☐ not started |
| 2 | Onboarding wizard | ☐ not started |
| 3 | AI output language | ☐ not started |
| 4 | Privacy notice (SV) | ☐ not started |
| 5 | Backend error strings (client map) | ☐ not started |
| 6 | Verification, docs, release | ☐ not started |

---

## Pending owner decisions (block parts of phase 3)

- [ ] **D1 — Tailored CV + cover letter language.** Recommended:
      follow the JOB AD's language (Swedish ad → Swedish application;
      English ad → English). UI language stays separate. *Not yet
      confirmed by owner.*
- [ ] **D2 — Match reasoning/verdicts language.** Recommended: the
      user's UI language. *Not yet confirmed.*
- [ ] **D3 — Dates/numbers** ("5h 38m", "2×") localize to sv-SE
      formatting in SV. Recommended: yes. *Not yet confirmed.*

---

## Phase 1 — Locale plumbing + console chrome (~1 day)

The console is ONE page (`/app`) with internal view switching — no
route duplication needed; it reads the stored preference.

Plumbing:
- [ ] `src/app/app/page.tsx`: resolve locale once on mount
      (`storedLocale() ?? 'en'`); pass down or context; set
      `document.documentElement.lang`.
- [ ] `src/components/Sidebar.tsx`: "Svenska/English" toggle in the
      footer slot (stores pref; no navigation — console is uniroute).
- [ ] Mobile top bar: same toggle.
- [ ] `src/i18n/dict.ts`: add `console` namespace (nav labels, view
      headers, buttons, empty states, notices).

Chrome string extraction (per file — replace literals with dict
lookups; tsc enforces EN/SV parity):
- [ ] `src/components/Sidebar.tsx` — NAV labels + group children
      (Dashboard, Matches, Awaiting you, Approved, Applications,
      Review & send, Sent, Profile, Settings, Beta feedback).
- [ ] `src/components/NextHunt.tsx` — "next automatic hunt at …",
      scheduler on/off copy (+ D3 time formatting if approved).
- [ ] `src/components/HuntPulse.tsx` — stage labels (Hunted/Matched/
      Awaiting you/In drafts/Sent) + hints ("ranked against your CV",
      "your move — approve or pass", …) + aria-labels.
- [ ] `src/app/app/page.tsx` — ViewHeader titles/subs for every
      view; dashboard notices (GLM banner, cap notices, hunt errors);
      `Warning` blocks; confirm dialogs; empty states; FeedbackView
      (chips Bug/Confusing/Missing feature/Idea/Love it, counter line,
      thank-you, send button).
- [ ] `src/app/error.tsx` — generic error screen copy.
- [ ] `src/components/MatchCard.tsx`, `ScoreRing.tsx`, `TierBadge.tsx`
      — tier labels (excellent/good/… match), "match" suffix.
- [ ] Verification: manual click-through EN+SV of every view; tsc +
      build; screenshot both languages.

## Phase 2 — Onboarding wizard (~0.5–1 day)

Highest-risk console flow (it drives hunt targeting) — its own phase
so it lands with focused testing.

- [ ] `src/components/OnboardingWizard.tsx` — step titles/subtitles
      (CV upload, country, region, municipalities, job titles,
      languages, review); "Current Search Titles" box labels; add/
      remove title controls + placeholders; AI-suggestion UI labels
      ("From your experience", "Worth a look", why-lines prefix);
      occupation-suggestions copy; back/next/finish buttons; the
      finish-error message; unsaved-changes guard text.
- [ ] `src/components/CvUpload.tsx` — drop-zone labels, format hint
      ("PDF or Word (.docx) up to 5MB"), uploading/success/error
      status copy (the "escalating status copy" per PROJECT_INDEX).
- [ ] Country/region/municipality data: country names (Sweden/
      United Kingdom → Sverige/Storbritannien); region + municipality
      names come from backend geo data (already Swedish for SE) —
      verify labels render correctly in both languages; UK names in
      SV where the geo feed is English.
- [ ] Verification: run a full onboarding in SV against the test
      backend; confirm saved profile identical to EN run.

## Phase 3 — AI output language (~1 day; D1/D2 gate parts)

Backend: a `ui_language` on the profile that prompts read.

- [ ] Migration: `profiles.ui_language` ('en' | 'sv', default 'en',
      nullable-safe) + model field (follow migration pattern of
      `cdc76cd3ae26`; new head).
- [ ] Onboarding saves `ui_language` from the wizard's locale; the
      toggle updates it for future runs (PUT /profile/me field).
- [ ] `app/services/ai_service.py` — verdict prompt: reasoning /
      have / missing / transfer text in `ui_language` (scores and
      tier stay language-free); suggestion prompts (search queries,
      worth-a-look "why") in `ui_language`.
- [ ] `app/services/draft_service.py` — tailored CV + cover letter
      language per **D1** (default plan: ad's language — detection is
      a prompt rule: "write in the language of the job ad", no
      classifier needed); fabrication-guard findings text follows the
      draft language so the review UI matches.
- [ ] `matcher_service` — no logic change; stored reasoning text is
      display-only. Existing matches stay English (re-scoring old
      rows burns AI spend for no benefit — agreed).
- [ ] Tests: prompt-construction tests assert the language
      instruction; one GLM-mocked round-trip per language; migration
      up/down on sqlite+postgres (CI covers both).
- [ ] Verification on prod (post-deploy): SV account runs a hunt →
      Swedish verdicts; approve a Swedish ad → Swedish cover letter.

## Phase 4 — Privacy notice in Swedish (~0.5 day)

- [ ] `src/app/privacy/page.tsx`: extract copy into the dict (or a
      parallel SV page component), translate all 8 sections verbatim-
      faithful (legal text: translate meaning, keep structure, same
      contact/emails).
- [ ] Route `src/app/sv/privacy/page.tsx` + SV metadata; landing +
      login + console footer links route per locale.
- [ ] Keep both versions in sync from now on — note added to
      CLAUDE.md ("privacy edits go into both languages").

## Phase 5 — Backend error strings, client-side map (~0.25 day)

- [ ] `src/lib/api.ts` `apiErrorMessage`: map the finite known
      messages (rate limits, email beta gate, CV validation, draft/
      guard messages) EN→SV when locale is sv; unknown messages pass
      through raw (never blank).
- [ ] Dict entries for the mapped strings; test with a forced 429.

## Phase 6 — Verification, docs, release (~0.5 day)

- [ ] Full click-through BOTH languages: landing → login → onboarding
      → first hunt → match decision → draft review → feedback page →
      privacy. Screenshot set for both.
- [ ] `docs/PROJECT_INDEX.md`: update file map (dict namespaces, new
      routes, ui_language); `README.md` mention dual-language; this
      doc's tracker closed out.
- [ ] GA events unaffected (names are language-free — verify no
      event name got translated).
- [ ] CLAUDE.md: note the parity rule ("every new console string
      lands in BOTH dict languages — tsc enforces").
- [ ] Deploy frontend (wrangler) + backend (Render manual deploy —
      migration runs on boot) and verify live.

---

## Standing rules for this effort

- Every new user-facing string lands in `dict.ts` under BOTH
  languages in the same PR — the `sv: typeof en` type makes a
  missing key a build failure, keep it that way.
- No route duplication for the console; `/sv` routes only where a
  page is statically exported (landing, login, privacy).
- Existing data (matches, drafts) is never re-translated.
- Each phase = its own PR(s) + CI + merge; update this doc's
  checkboxes in the same PR so sessions never lose position.
