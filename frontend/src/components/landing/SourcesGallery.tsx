// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same sources gallery. Named sites stay out of the copy; the
// framing is coverage and precision. The privacy line states only what
// the privacy notice states (EU storage; CV text to the AI outside the
// EU) and links the full notice.

import Link from 'next/link';

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function SourcesGallery({
  t = dicts.en,
  privacyPath = '/privacy',
}: {
  t?: Dict;
  privacyPath?: string;
}) {
  const s = t.sourcesSec;

  return (
    <div className="scroll-mt-24 border-t border-line bg-surface-2/50">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <Reveal>
          <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
            {s.h2}
          </h2>
        </Reveal>
        <Reveal delay={90}>
          <div
            role="region"
            aria-label={s.ariaLabel}
            tabIndex={0}
            className="no-scrollbar -mx-6 mt-10 flex snap-x snap-mandatory gap-5 overflow-x-auto px-6 pb-2"
          >
            {s.cards.map((card) => (
              <div
                key={card.title}
                className="min-w-[85%] snap-start rounded-2xl border border-line bg-surface p-8 sm:min-w-[340px]"
              >
                <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/45">
                  {card.tag}
                </p>
                <h3 className="mt-4 font-display text-2xl font-semibold tracking-tight text-paper">
                  {card.title}
                </h3>
                <p className="mt-3 text-sm leading-relaxed text-paper/60">{card.body}</p>
              </div>
            ))}
          </div>
        </Reveal>
        <Reveal delay={150}>
          <p className="mt-10 text-sm leading-relaxed text-paper/50">
            {s.privacyA}{' '}
            <Link
              href={privacyPath}
              className="font-medium text-paper/80 underline underline-offset-4 transition hover:text-paper"
            >
              {s.privacyLink}
            </Link>
            .
          </p>
        </Reveal>
      </div>
    </div>
  );
}
