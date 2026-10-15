'use client';

// WO-28 + owner feedback 2026-10-15: the interactive score slider is
// GONE. The product lets you SET a minimum score once in onboarding;
// it does not offer live feed-filtering by drag — and the landing must
// never demo an interaction the console doesn't have. What remains is
// what the console really shows: verdict cards with evidence chips
// (have / gap / carries — the real MatchCard columns), and the
// approval gate — drafts you inspect before anything is sent. The
// badge keeps the illustration honest: sample jobs, a sample CV.

import { useState } from 'react';
import { ArrowRight, Check, X } from 'lucide-react';

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function HuntDemo({ t = dicts.en }: { t?: Dict }) {
  const d = t.demo;
  const [showDraft, setShowDraft] = useState(false);

  return (
    <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
      <Reveal>
        <p className="num flex items-center gap-2.5 text-[10px] uppercase tracking-[0.16em] text-paper/45">
          <span className="rounded border border-paper/25 px-1.5 py-0.5 text-paper/60">
            {d.badge}
          </span>
          {d.h2}
        </p>
        <p className="mt-5 max-w-2xl text-lg leading-relaxed text-paper/70">
          {d.sub}
        </p>
      </Reveal>

      <Reveal delay={90}>
        <div className="mt-10 rounded-2xl border border-line bg-surface p-6 sm:p-8">
          <ul className="space-y-4">
            {d.cards.map((card) => (
              <li
                key={card.title}
                className="rounded-xl border border-line bg-ink p-5 sm:p-6"
              >
                <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
                  <div>
                    <p className="font-display text-lg font-semibold tracking-tight text-paper">
                      {card.title}
                    </p>
                    <p className="num mt-1 text-xs text-paper/50">
                      {card.score} · {d.scoreWord}
                    </p>
                  </div>
                  <span
                    className={`num rounded-full border px-3 py-1 text-[10px] uppercase tracking-[0.14em] ${
                      card.verdict === 'apply'
                        ? 'border-ok/30 text-ok'
                        : card.verdict === 'marginal'
                          ? 'border-signal/30 text-signal'
                          : 'border-line text-paper/40'
                    }`}
                  >
                    {d.verdicts[card.verdict]}
                  </span>
                </div>
                {/* Evidence chips: what the verdict is made of — the
                    sample CV's haves, gaps, and transfers. */}
                <ul className="mt-4 flex flex-wrap gap-2">
                  {card.chips.map((chip) => (
                    <li
                      key={chip.text}
                      className={`num flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] ${
                        chip.kind === 'inCv'
                          ? 'border-ok/25 text-ok/90'
                          : chip.kind === 'gap'
                            ? 'border-bad/25 text-bad/90'
                            : 'border-signal/25 text-signal/90'
                      }`}
                    >
                      {chip.kind === 'inCv' ? (
                        <Check className="h-3 w-3" aria-hidden />
                      ) : chip.kind === 'gap' ? (
                        <X className="h-3 w-3" aria-hidden />
                      ) : (
                        <ArrowRight className="h-3 w-3" aria-hidden />
                      )}
                      <span className="text-paper/80">{chip.text}</span>
                      <span className="text-paper/40">{d.chips[chip.kind]}</span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>

          {/* The approval gate, made visible: what would be sent is a
              draft you look at first — here, a sample opening. */}
          <button
            type="button"
            onClick={() => setShowDraft((v) => !v)}
            aria-expanded={showDraft}
            className="num mt-8 rounded-full border border-line px-4 py-2 text-[11px] uppercase tracking-[0.14em] text-paper/70 transition hover:border-paper/30 hover:text-paper"
          >
            {showDraft ? d.hideDrafts : d.showDrafts}
          </button>
          {showDraft && (
            <div className="mt-4 rounded-xl border border-line bg-ink p-5">
              <p className="font-display text-base leading-relaxed text-paper/85">
                {d.draftSample}
              </p>
              <p className="num mt-3 border-t border-line pt-3 text-[10px] uppercase tracking-[0.14em] text-paper/40">
                {d.draftLabel}
              </p>
            </div>
          )}
        </div>
      </Reveal>
    </div>
  );
}
