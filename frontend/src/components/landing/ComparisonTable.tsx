// WO-28: the comparison table. The middle column attacks the CATEGORY
// PATTERN ("typical auto-apply tool"), never a named competitor — the
// rows describe what auto-apply services do by design (volume, score
// opacity, keyword stuffing, login-walled scraping), and our column
// states only mechanisms we actually ship: approval gates (WO-14),
// duplicate collapse (WO-18), verdict columns, the fabrication guard,
// official feeds. Rows are dict data so both languages stay in step.

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function ComparisonTable({ t = dicts.en }: { t?: Dict }) {
  const c = t.comparison;

  return (
    <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
      <Reveal>
        <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
          {c.h2}
        </h2>
        <p className="mt-5 max-w-2xl text-lg leading-relaxed text-paper/70">
          {c.sub}
        </p>
      </Reveal>
      <Reveal delay={90}>
        <div className="mt-10 overflow-x-auto rounded-2xl border border-line">
          <table className="w-full min-w-[560px] border-collapse bg-surface text-left">
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="num px-5 py-4 text-[10px] uppercase tracking-[0.16em] text-paper/45">
                  {c.colFeature}
                </th>
                <th scope="col" className="num px-5 py-4 text-[10px] uppercase tracking-[0.16em] text-paper/45">
                  {c.colThem}
                </th>
                <th scope="col" className="num border-l border-signal/20 bg-signal/5 px-5 py-4 text-[10px] uppercase tracking-[0.16em] text-signal">
                  {c.colUs}
                </th>
              </tr>
            </thead>
            <tbody>
              {c.rows.map(([feature, them, us]) => (
                <tr key={feature} className="border-b border-line last:border-b-0">
                  <td className="px-5 py-4 text-sm font-medium text-hi">
                    {feature}
                  </td>
                  <td className="px-5 py-4 text-sm leading-relaxed text-paper/50">
                    {them}
                  </td>
                  <td className="border-l border-signal/20 bg-signal/5 px-5 py-4 text-sm font-medium leading-relaxed text-paper">
                    {us}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Reveal>
    </div>
  );
}
