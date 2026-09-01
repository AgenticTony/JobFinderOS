// Locale plumbing for the two-language marketing surface. Static
// export on Cloudflare Pages has no server logic, so first-visit
// detection runs client-side on the ENGLISH routes only: a browser
// whose preferred language is Swedish lands on /sv. An explicit URL
// always wins over detection — /sv never redirects away — and the
// footer toggle stores the user's choice so it sticks.

import type { Locale } from './dict';

const LANG_KEY = 'jfos-lang';

export function storedLocale(): Locale | null {
  try {
    const v = localStorage.getItem(LANG_KEY);
    if (v === 'en' || v === 'sv') return v;
  } catch {
    // private mode / storage disabled — fall through to detection
  }
  return null;
}

export function storeLocale(locale: Locale) {
  try {
    localStorage.setItem(LANG_KEY, locale);
  } catch {
    // best effort — the URL itself still carries the choice
  }
}

// True when the browser's own language list leads with Swedish and the
// user has never expressed a preference here.
export function shouldRedirectToSv(): boolean {
  if (storedLocale() === 'en') return false;
  if (storedLocale() === 'sv') return true;
  const langs =
    typeof navigator !== 'undefined' && navigator.languages
      ? navigator.languages
      : typeof navigator !== 'undefined' && navigator.language
        ? [navigator.language]
        : [];
  return langs.some((l) => l.toLowerCase().startsWith('sv'));
}

// The toggle: remember the choice, then swap the language-scoped path
// (keeping any query string, e.g. /login?mode=register). Navigates
// immediately — deliberately NO analytics event here: the GA container
// initializes ~5s after a cold page load (measured: tfd 5.1-7.2s), so
// any event pushed from this click races a navigation the user is
// actively waiting on, and waiting for the container makes the toggle
// feel broken for a signal GA already has — /sv page paths ARE the
// language-interest metric. The five in-app funnel events
// (signup/onboarding/decision/hunt/feedback) fire on settled pages
// and deliver reliably.
export function switchLocale(to: Locale, currentPath: string): void {
  storeLocale(to);
  const query = window.location.search;
  window.location.href =
    to === 'sv'
      ? `/sv${currentPath === '/' ? '' : currentPath}${query}`
      : `${currentPath.replace(/^\/sv(?=\/|$)/, '') || '/'}${query}`;
}
