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
// (keeping any query string, e.g. /login?mode=register). The analytics
// wrinkle, production-proven: GTM/gtag initialize ~5s after a cold
// page load (tfd≈5100ms in the collect URLs) — an event pushed before
// the container is live sits queued, and navigating destroys it, so
// the language_switched hit is lost. So: wait for the container
// (google_tag_manager appears once gtm.js has processed), THEN send
// with event_callback (fires once the hit is out) and navigate on the
// callback, with fallbacks so nothing can ever trap the user.
export function switchLocale(to: Locale, currentPath: string): void {
  storeLocale(to);
  const query = window.location.search;
  const href =
    to === 'sv'
      ? `/sv${currentPath === '/' ? '' : currentPath}${query}`
      : `${currentPath.replace(/^\/sv(?=\/|$)/, '') || '/'}${query}`;

  let navigated = false;
  const navigate = () => {
    if (navigated) return;
    navigated = true;
    window.location.href = href;
  };

  const send = () => {
    if (typeof window.gtag === 'function') {
      window.gtag('event', 'language_switched', {
        to,
        event_callback: navigate,
      });
    }
    // Container live: the callback is the fast path; 1.5s covers a
    // container that processed the push but never invoked the callback.
    setTimeout(navigate, 1500);
  };

  // No analytics consent -> nothing to wait for, navigate now.
  if (!window.Cookiebot?.consent?.statistics) {
    navigate();
    return;
  }

  let tries = 0;
  const trySend = () => {
    if (navigated) return;
    if (window.google_tag_manager || tries >= 40) {
      send();
    } else {
      tries += 1;
      setTimeout(trySend, 100);
    }
  };
  trySend();
}
