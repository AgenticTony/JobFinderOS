// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same wide verdict block. The screenshot is a real verdict, and
// the caption says so — that is the point of the section.

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function VerdictWide({ t = dicts.en }: { t?: Dict }) {
  return (
    <div className="border-t border-line">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
        <Reveal>
          <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
            {t.verdict.h2}
          </h2>
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-paper/70">
            {t.verdict.body}
          </p>
        </Reveal>
        <Reveal delay={120}>
          <figure className="mt-12 overflow-hidden rounded-2xl border border-line shadow-[0_24px_60px_-24px_rgba(0,0,0,0.7)]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/screenshots/match-detail.png"
              alt={t.verdict.alt}
              className="block w-full"
              width={776}
              height={402}
            />
          </figure>
          <p className="num mt-4 text-[10px] uppercase tracking-[0.16em] text-paper/40">
            {t.verdict.caption}
          </p>
        </Reveal>
      </div>
    </div>
  );
}
