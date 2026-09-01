// GA4 event tracking for the beta (owner decision 2026-09-01).
//
// Consent-safe by construction: gtag only exists on the page after
// AnalyticsGate mounted GTM/GA4, which only happens once Cookiebot
// statistics consent was given — so every call below is a silent
// no-op for visitors who declined (or before they decide).
//
// The console is one URL with client-side view switching, so GA4's
// automatic pageviews see nothing after /app — these events ARE the
// product funnel. Keep names/params stable; tick signup_completed and
// onboarding_completed as KEY EVENTS in GA4 Admin → Events so they
// report as conversions.

type EventParams = Record<string, string | number | boolean | undefined>;

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
  }
}

export function track(event: string, params?: EventParams) {
  window.gtag?.('event', event, params);
}
