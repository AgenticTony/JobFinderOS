// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same three steps beside the sticky hunt-pulse screenshot.

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function Steps({ t = dicts.en }: { t?: Dict }) {
  return (
    <div className="mx-auto max-w-6xl px-6 pb-28 pt-16 sm:pt-20">
      <div className="grid gap-16 lg:grid-cols-[0.9fr_1.1fr] lg:gap-20">
        <div className="lg:sticky lg:top-28 lg:self-start">
          <Reveal>
            <figure className="overflow-hidden rounded-2xl border border-line shadow-[0_24px_60px_-24px_rgba(0,0,0,0.7)]">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/screenshots/hunt-pulse.png"
                alt={t.hero.pulseAlt}
                className="block w-full"
                width={786}
                height={1226}
              />
            </figure>
            <p className="num mt-3 text-[10px] uppercase tracking-[0.16em] text-paper/50">
              {t.verdict.pulseCaption}
            </p>
          </Reveal>
        </div>

        <div>
          <Reveal>
            <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
              {t.verdict.h2a}
              <br />
              {t.verdict.h2b}
            </h2>
          </Reveal>
          <ol className="mt-12">
            {t.steps.map((step, i) => (
              <Reveal key={step.title} delay={i * 90}>
                <li className="grid gap-2.5 border-t border-line py-7 sm:grid-cols-[4.5rem_1fr] sm:gap-8">
                  <p className="num pt-1.5 text-sm text-paper/35">
                    {String(i + 1).padStart(2, '0')}
                  </p>
                  <div>
                    <h3 className="font-display text-xl font-semibold tracking-tight text-paper">
                      {step.title}
                    </h3>
                    <p className="mt-2 max-w-md leading-relaxed text-paper/70">
                      {step.body}
                    </p>
                  </div>
                </li>
              </Reveal>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
