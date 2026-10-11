# Handoff — console Swedish i18n (branch feat/console-i18n-sv, 2026-10-12)

Goal: console (`app/app/page.tsx`), wizard (`OnboardingWizard.tsx`) and
`/privacy` in Swedish + English via `i18n/dict.ts`; console reads the
saved language. Reverses the 2026-09-01 "console stays English" decision
(update the header comment in dict.ts).

Done: `resolveLocale` / `consoleLocale` in `i18n/locale.ts`;
`i18n/LocaleContext.tsx` (`useT`, `useLocale`). Not yet wired anywhere.

Left:
1. dict.ts: add `console`, `wizard`, `privacy` sections to en AND sv
   (`sv: Dict` makes a missing key a tsc error). Interpolated strings as
   functions, e.g. `matchesNeed: (n: number) => ...`.
2. Console: state `locale` set in the session effect from
   `consoleLocale()`, wrap tree in `LocaleContext.Provider`, set
   `document.documentElement.lang`, add a language toggle (Settings view +
   sidebar footer; mobile has no sidebar) that calls `storeLocale`.
3. Replace strings: page.tsx (~180, incl. window.confirm texts, error
   prefixes, NAV labels in Sidebar.tsx — NAV is a module const, make
   labels keys), MatchCard, HuntPulse, NextHunt (formatRemaining),
   CvUpload, TierBadge (`TIER_CONFIG.label` in lib/utils.ts), `timeAgo`
   (add locale param), error.tsx (outside provider: use consoleLocale()),
   OnboardingWizard (STEPS, WORK_RIGHTS_OPTIONS, LANGUAGE_OPTIONS display
   labels only — the stored values must stay English, MODES, radius).
4. /privacy: extract to a PrivacyView({locale}), add `app/sv/privacy`;
   sv login footer + console Settings link must point to /sv/privacy.
5. LoginView: `storeLocale(locale)` on successful sign-in/sign-up so a
   Swedish signup lands in a Swedish console.
6. Tests: node --test (Node 24 strips types) for resolveLocale, dict
   parity (no empty sv strings, function entries callable), and a
   scanner for leftover English JSX text in console files; wire into
   the CI frontend job.
7. Out of scope (stays English): backend error/notice text, AI match
   reasoning.
