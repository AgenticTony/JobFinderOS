'use client';

// FE-21: root error boundary (Next.js App Router `error.tsx`). Catches
// render/lifecycle errors anywhere below the root layout and shows a
// on-design fallback instead of a blank page or a React stack trace.
//
// Static-export constraints (next.config.ts `output: 'export'`): an
// error boundary runs in the BROWSER, so it must be a client component —
// no server exports (metadata), no server-only APIs, and `reset()` is
// the client-side segment re-render Next provides. No 'use server' code
// paths, no dynamic imports of server modules: safe to ship as static
// chunks. Styled purely with the Hunting Console token system so it
// inherits globals.css from the root layout it renders inside.

import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { dicts, type Locale } from '@/i18n/dict';
import { consoleLocale } from '@/i18n/locale';

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // The root boundary sits OUTSIDE the console's LocaleContext (it can
  // catch the provider itself failing), so it resolves the language on
  // its own: a /sv page is Swedish, otherwise the console's rule (saved
  // choice, then browser). English until mounted — static export.
  const [locale, setLocale] = useState<Locale>('en');
  useEffect(() => {
    setLocale(window.location.pathname.startsWith('/sv') ? 'sv' : consoleLocale());
  }, []);
  const t = dicts[locale].console.errorPage;

  useEffect(() => {
    // The boundary must log — a silently swallowed render error is
    // undiagnosable from user reports alone.
    console.error('[JobFinderOS] UI error caught by boundary:', error);
  }, [error]);

  return (
    <div className="console-backdrop flex min-h-dvh flex-col items-center justify-center gap-4 bg-ink px-6 text-center text-hi">
      <AlertTriangle className="h-8 w-8 text-bad" aria-hidden />
      <h2 className="font-semibold tracking-tight">{t.title}</h2>
      <p className="max-w-md text-sm text-mid">{t.body}</p>
      {error.digest && <p className="text-xs text-low">{t.reference(error.digest)}</p>}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
        <button
          onClick={reset}
          className="rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98]"
        >
          {t.tryAgain}
        </button>
        <a
          href="/app"
          className="rounded-lg border border-line px-4 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
        >
          {t.reload}
        </a>
      </div>
    </div>
  );
}
