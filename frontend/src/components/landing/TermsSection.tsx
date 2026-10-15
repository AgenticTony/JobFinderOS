// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same inverted-billing terms. In beta there is no billing at all;
// the status line keeps that honest.

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function TermsSection({ t = dicts.en }: { t?: Dict }) {
  return (
    <div className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-12 px-6 py-20 lg:grid-cols-2 lg:gap-20">
        <Reveal>
          <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
            {t.terms.h2a}
            <br />
            {t.terms.h2b}
          </h2>
          <p className="num mt-6 max-w-xs text-xs uppercase leading-relaxed tracking-[0.14em] text-paper/45">
            {t.terms.note}
          </p>
        </Reveal>
        <Reveal delay={120}>
          <ul className="divide-y divide-line text-[17px] leading-relaxed text-paper/80">
            {t.terms.items.map((item) => (
              <li key={item} className="py-4">
                {item}
              </li>
            ))}
          </ul>
        </Reveal>
      </div>
    </div>
  );
}
