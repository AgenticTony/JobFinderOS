'use client';

// WO-28: the interactive hunt demo. Everything here is ILLUSTRATIVE —
// sample jobs, a sample CV's evidence chips, a sample draft opening;
// no real user's data appears. The mechanisms it depicts are real:
// the minimum-score threshold gates what reaches you (onboarding
// setting), and drafts are per-listing artifacts you approve before
// anything is sent. The badge keeps the illustration honest.

import { useState } from 'react';
import { ArrowRight, Check, X } from 'lucide-react';

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

// Scores 84/71/58: at the default threshold 60, two of three cards
// clear the bar — dragging up prunes to one, dragging down admits the
// skip verdict. The one number a visitor should take away: a higher
// bar means fewer, better matches.
const DEFAULT_THRESHOLD = 60;

export default function HuntDemo({ t = dicts.en }: { t?: Dict }) {
  const d = t.demo;
  const [threshold, setThreshold] = useState(DEFAULT_THRESHOLD);
  const [showDraft, setShowDraft] = useState(false);

  const kept = d.cards.filter((c) => c.score >= threshold);

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
          {/* The slider: the onboarding minimum-score setting, made
              tangible. aria-live announces the changing count. */}
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
            <label
              htmlFor="demo-min-score"
              className="num text-[10px] uppercase tracking-[0.16em] text-paper/45"
            >
              {d.minScoreLabel}
            </label>
            <p aria-live="polite" className="num text-sm text-paper/70">
              {d.reachLine(threshold, kept.length, d.cards.length)}
            </p>
          </div>
          <input
            id="demo-min-score"
            type="range"
            min={55}
            max={90}
            step={1}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            aria-label={d.minScoreLabel}
            className="mt-4 w-full accent-signal"
          />

          <ul className="mt-8 space-y-4">
            {kept.map((card) => (
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
