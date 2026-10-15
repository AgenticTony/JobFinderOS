// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same close. "Tomorrow morning" is conservative-true: hunts run
// twice daily, so the worst case for a new signup is the next morning
// run — an earlier signup sees matches the same evening.

import Link from 'next/link';

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function FinalCta({
  t = dicts.en,
  href = '/app',
}: {
  t?: Dict;
  href?: string;
}) {
  return (
    <div className="mx-auto max-w-6xl px-6 py-28 text-center">
      <Reveal>
        <h2 className="font-display text-[clamp(2.5rem,5.5vw,4.5rem)] font-semibold leading-[1.05] tracking-[-0.022em] text-paper">
          {t.final.h2a}
          <br />
          {t.final.h2b}
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-lg text-paper/55">{t.final.sub}</p>
        <Link
          href={href}
          className="mt-10 inline-block rounded-full bg-signal px-8 py-3.5 text-[15px] font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
        >
          {t.final.cta}
        </Link>
      </Reveal>
    </div>
  );
}
