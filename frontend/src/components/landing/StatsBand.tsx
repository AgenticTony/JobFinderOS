// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same band instead of copying it. The numbers here are the only
// stats the landing may state: counts we can defend from the system.

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function StatsBand({ t = dicts.en }: { t?: Dict }) {
  return (
    <div className="border-b border-line">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-x-6 gap-y-10 px-6 pb-14 pt-20 sm:grid-cols-4 sm:pt-24">
        {t.stats.map(([value, label]) => (
          <Reveal key={label}>
            <p className="num font-display text-4xl font-semibold tracking-tight text-paper sm:text-[2.75rem]">
              {value}
            </p>
            <p className="num mt-2 text-[10px] uppercase tracking-[0.16em] text-paper/45">
              {label}
            </p>
          </Reveal>
        ))}
      </div>
    </div>
  );
}
