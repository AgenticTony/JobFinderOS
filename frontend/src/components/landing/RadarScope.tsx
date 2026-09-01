'use client';

// The hero's giant scope: a radar arc cropped by the viewport edges,
// one slow amber sweep, three contacts, and the score chip counting up
// where the arc crests. Decorative by intent, aria-hidden as a set.
//
// The contact FIELD (owner decision 2026-09-01): ~24 job titles across
// EVERY industry — not just tech; the product hunts roles for everyone,
// postman to pharmacist. Each contact is a small pill like the score
// chip (same language, quieter voice) with its own match score, placed
// by hand on staggered arc rows INSIDE the visible top-cap of the
// circle — clear of the score chip's corner, clear of each other.
// Hand-placed coordinates (not Math.random) keep SSR and client render
// byte-identical. Depth comes from three border/opacity tiers.

import { useEffect, useState } from 'react';

const FINAL_SCORE = 87;

// [label, score, x, y] — pill CENTRE, in % of the 150vmin scope.
// Rows follow the arc: the circle narrows towards the crest, so upper
// rows sit tighter to the middle; the score chip owns x 12..48, y 5..13.
const FIELD: [string, number, number, number][] = [
  // Upper arc, clear of the chip on the left.
  ['Nurse', 82, 36, 4], ['Teacher', 76, 45, 4],
  ['Chef', 73, 54, 4], ['Pilot', 84, 63, 4],
  ['Vet nurse', 76, 35, 9], ['Pharmacist', 81, 56, 9],
  ['Accountant', 79, 64, 9], ['Optician', 79, 71, 9],
  // Mid arc — chip cleared, full width of the cap.
  ['Postman', 68, 31, 14], ['Electrician', 71, 41, 14],
  ['IT support', 78, 51, 14], ['Physio', 83, 61, 14],
  ['Midwife', 81, 71, 14],
  ['Police officer', 70, 32, 19], ['Firefighter', 65, 42, 19],
  ['Bus driver', 64, 52, 19], ['Journalist', 67, 62, 19],
  ['Social worker', 74, 71, 19],
  // Lower arc, drifting wider as the cap opens out.
  ['Barista', 57, 27, 24], ['Plumber', 62, 36, 24],
  ['Welder', 59, 46, 24], ['Hairdresser', 56, 56.5, 24],
  ['Gardener', 55, 66, 24], ['Courier', 63, 74, 24],
];

const TIER_STYLES = [
  'border-signal/20 text-paper/60',
  'border-paper/[0.13] text-paper/50',
  'border-paper/[0.08] text-paper/40',
];

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

        {/* The scope makes an argument: this contact is tonight's match.
            A ROLE, scored against your CV — the chip names the function,
            not a company. */}
        <div className="absolute left-[30%] top-[9%] -translate-x-1/2 -translate-y-[calc(100%+14px)]">
          <div className="flex items-center gap-3.5 rounded-full border border-signal/25 bg-ink/85 py-2.5 pl-5 pr-6 backdrop-blur-md">
            <span className="num text-[11px] uppercase tracking-[0.16em] text-paper/55">
              Backend developer · fintech
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

        {/* The field: two dozen scored roles across every industry —
            the whole labour market on scope. Each is a miniature of the
            score chip (role · score in a pill), centre-anchored on its
            hand-set coordinate; three tiers give the depth. */}
        {FIELD.map(([label, fieldScore, x, y], i) => (
          <div
            key={i}
            className={`absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full border bg-ink/80 py-1 pl-3 pr-2.5 backdrop-blur-md ${TIER_STYLES[i % 3]}`}
            style={{ left: `${x}%`, top: `${y}%` }}
          >
            <span className="num text-[9px] uppercase tracking-[0.16em]">
              {label}
            </span>
            <span className="num ml-2 text-[9px] font-semibold text-signal/80">
              {fieldScore}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
