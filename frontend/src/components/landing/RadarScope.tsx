'use client';

// The hero's giant scope: a radar arc cropped by the viewport edges,
// one slow amber sweep, three contacts, and the score chip counting up
// where the arc crests. Decorative by intent, aria-hidden as a set.

import { useEffect, useState } from 'react';

const FINAL_SCORE = 87;

export default function RadarScope() {
  // SSR renders the final score; the count-up only starts on the client.
  const [score, setScore] = useState(FINAL_SCORE);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const duration = 1400;
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
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute left-1/2 top-[58%] aspect-square w-[150vmin] -translate-x-1/2">
        <div className="absolute inset-0 rounded-full border border-signal/[0.14]" />
        <div className="absolute inset-[16%] rounded-full border border-signal/[0.09]" />
        <div className="absolute inset-[32%] rounded-full border border-signal/[0.07]" />
        <div
          className="radar-sweep absolute inset-0 rounded-full will-change-transform"
          style={{
            background:
              'conic-gradient(from 0deg, rgba(245,165,36,0.20), rgba(245,165,36,0.03) 60deg, transparent 80deg)',
          }}
        />
        {/* Contacts along the visible arc. */}
        <span className="absolute left-[30%] top-[9%] flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal/60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
        </span>
        <span className="absolute left-[64%] top-[17%] h-1.5 w-1.5 rounded-full bg-signal/50" />
        <span className="absolute left-[14%] top-[24%] h-1.5 w-1.5 rounded-full bg-signal/35" />

        {/* The scope makes an argument: this contact is tonight's match. */}
        <div className="absolute left-[30%] top-[9%] -translate-x-1/2 -translate-y-[calc(100%+14px)]">
          <div className="flex items-center gap-3.5 rounded-full border border-signal/25 bg-ink/85 py-2.5 pl-5 pr-6 backdrop-blur-md">
            <span className="num text-[11px] uppercase tracking-[0.16em] text-paper/55">
              Pågen · Malmö
            </span>
            <span className="h-3.5 w-px bg-signal/25" aria-hidden />
            <span
              suppressHydrationWarning
              className="num text-2xl font-semibold leading-none text-signal"
            >
              {score}
            </span>
            <span className="num text-[10px] uppercase tracking-[0.16em] text-paper/45">
              match
            </span>
          </div>
          {/* Connector down to the contact. */}
          <span className="absolute left-1/2 top-full h-[13px] w-px -translate-x-1/2 bg-signal/30" />
        </div>

        {/* A second contact, quieter: the hunt has breadth. */}
        <p className="num absolute left-[64%] top-[17%] ml-4 text-[9px] uppercase tracking-[0.16em] text-paper/35">
          Lund · 74
        </p>
      </div>
    </div>
  );
}
