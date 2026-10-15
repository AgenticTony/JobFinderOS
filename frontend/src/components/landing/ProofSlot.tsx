// WO-28: the social-proof slot — deliberately a PLACEHOLDER. The
// category's pages open with testimonials we cannot honestly show yet
// (three beta users, no quotes collected). Instead of a fabricated
// quote, the card says exactly what will fill it and on what terms:
// a real result, the user's own words, with permission. The dashed
// border keeps it visually marked as pending, not performative.

import { Quote } from 'lucide-react';

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function ProofSlot({ t = dicts.en }: { t?: Dict }) {
  const p = t.proof;

  return (
    <div className="mx-auto max-w-4xl px-6 py-24 sm:py-28">
      <Reveal>
        <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/45">
          {p.kicker}
        </p>
        <figure className="mt-6 rounded-2xl border border-dashed border-line bg-surface/60 p-8 sm:p-10">
          <Quote className="h-6 w-6 text-paper/30" aria-hidden />
          <blockquote className="mt-5 font-display text-xl font-medium leading-relaxed text-paper/75 sm:text-2xl">
            {p.placeholderQuote}
          </blockquote>
          <figcaption className="num mt-5 text-[10px] uppercase tracking-[0.16em] text-paper/40">
            {p.placeholderAttr}
          </figcaption>
        </figure>
        <p className="mt-6 max-w-2xl text-sm leading-relaxed text-paper/50">
          {p.note}
        </p>
      </Reveal>
    </div>
  );
}
