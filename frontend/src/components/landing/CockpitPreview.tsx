'use client';

// The landing signature piece: the real match-card UI as a component
// preview (not a screenshot fake), with the radar sweep and a score
// count-up as the single orchestrated moment. All motion dies under
// prefers-reduced-motion (globals.css kills CSS, the counter checks it
// itself and renders the final value immediately).

import { useEffect, useState } from 'react';

const FINAL_SCORE = 87;

export default function CockpitPreview() {
  // Server renders the final score; the count-up only starts on the
  // client, so suppressHydrationWarning covers the first mismatch frame.
  const [score, setScore] = useState(FINAL_SCORE);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const duration = 1300;
    let raf = 0;
    setScore(0);
    const tick = (t: number, start: number) => {
      const p = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setScore(Math.round(FINAL_SCORE * eased));
      if (p < 1) raf = requestAnimationFrame((tt) => tick(tt, start));
    };
    raf = requestAnimationFrame((t) => tick(t, t));
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div
      role="group"
      aria-label="Preview of a scored match in the JobFinderOS console"
      className="relative overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl shadow-ink/25"
    >
      {/* Radar strip: the hunt, made visible. */}
      <div className="relative h-32 border-b border-line bg-ink sm:h-36" aria-hidden>
        <svg
          viewBox="0 0 320 144"
          preserveAspectRatio="xMidYMax slice"
          className="absolute inset-0 h-full w-full"
        >
          {[46, 92, 138, 184].map((r) => (
            <circle
              key={r}
              cx="160"
              cy="144"
              r={r}
              fill="none"
              stroke="#1e2330"
              strokeWidth="1"
            />
          ))}
          <line x1="0" y1="144" x2="320" y2="144" stroke="#1e2330" strokeWidth="1" />
          <line x1="160" y1="0" x2="160" y2="144" stroke="#1e2330" strokeWidth="1" />
        </svg>
        <div
          className="radar-sweep absolute bottom-0 left-1/2 h-[280px] w-[280px] -translate-x-1/2 rounded-full"
          style={{
            background:
              'conic-gradient(from 0deg, rgba(245,165,36,0.28), rgba(245,165,36,0.04) 55deg, transparent 75deg)',
          }}
        />
        {/* Contacts on the scope. */}
        <span className="absolute left-[64%] top-[30%] flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal/60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
        </span>
        <span className="absolute left-[33%] top-[58%] h-1.5 w-1.5 rounded-full bg-signal/50" />
        <span className="absolute left-[81%] top-[64%] h-1.5 w-1.5 rounded-full bg-signal/40" />
        <p className="num absolute bottom-2.5 left-4 text-[10px] uppercase tracking-[0.16em] text-low">
          scope · 3 contacts
        </p>
      </div>

      {/* Card body: mirrors the console's MatchCard vocabulary. */}
      <div className="p-5 sm:p-6">
        <div className="mb-4 flex items-center justify-between">
          <p className="num text-[10px] uppercase tracking-[0.16em] text-low">
            Hunt 46 · 21:00 CEST
          </p>
          <p className="num flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-signal">
            <span className="h-1.5 w-1.5 rounded-full bg-signal" aria-hidden />
            Live
          </p>
        </div>

        <h3 className="text-lg font-semibold tracking-tight text-hi">
          Baker, evening shift
        </h3>
        <p className="mt-0.5 text-sm text-mid">Pågen · Malmö · posted 2h ago</p>

        <div className="mt-5 flex items-end gap-4">
          <span
            suppressHydrationWarning
            className="num text-4xl font-semibold leading-none text-signal"
          >
            {score}
          </span>
          <div className="flex-1 pb-0.5">
            <div className="h-1.5 overflow-hidden rounded-full bg-line-2">
              <div
                className="h-full rounded-full bg-signal transition-[width] duration-700 ease-out"
                style={{ width: `${FINAL_SCORE}%` }}
              />
            </div>
            <p className="num mt-1.5 text-[10px] uppercase tracking-[0.16em] text-low">
              Match score · strong
            </p>
          </div>
        </div>

        <p className="num mt-5 text-[10px] uppercase tracking-[0.16em] text-low">
          Verdict
        </p>
        <p className="mt-1.5 text-sm leading-relaxed text-mid">
          Five years of bakery production and a food-safety certificate. Direct
          employer ad; the agency cross-post was merged automatically.
        </p>

        {/* Preview only: styled spans, never focusable. */}
        <div className="mt-5 flex gap-2" aria-hidden>
          <span className="rounded-lg bg-signal px-4 py-2 text-[13px] font-semibold text-ink">
            Tailor CV
          </span>
          <span className="rounded-lg border border-line-2 px-4 py-2 text-[13px] font-medium text-mid">
            Skip
          </span>
        </div>
      </div>
    </div>
  );
}
