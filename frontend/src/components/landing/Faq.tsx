// WO-28: the FAQ. Native <details>/<summary> accordions — keyboard and
// screen-reader accessible with zero JS state. Every answer is a claim:
// the data answer matches the privacy notice (EU storage, CV text to the
// AI outside the EU), the language answer matches the tailor prompt
// (ai_service.py: "write both documents in the language of the job
// posting"), and the cost answer restates the published beta terms.

import { ChevronDown } from 'lucide-react';

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function Faq({ t = dicts.en }: { t?: Dict }) {
  const f = t.faq;

  return (
    <div className="mx-auto max-w-4xl px-6 py-24 sm:py-28">
      <Reveal>
        <h2
          id="faq"
          className="scroll-mt-24 font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper"
        >
          {f.h2}
        </h2>
      </Reveal>
      <Reveal delay={90}>
        <div className="mt-10">
          {f.items.map((item) => (
            <details key={item.q} className="group border-t border-line last:border-b">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 text-[17px] font-medium text-paper transition hover:text-paper [&::-webkit-details-marker]:hidden">
                {item.q}
                <ChevronDown
                  className="h-4 w-4 shrink-0 text-paper/40 transition-transform group-open:rotate-180"
                  aria-hidden
                />
              </summary>
              <p className="max-w-2xl pb-6 leading-relaxed text-paper/70">
                {item.a}
              </p>
            </details>
          ))}
        </div>
      </Reveal>
    </div>
  );
}
