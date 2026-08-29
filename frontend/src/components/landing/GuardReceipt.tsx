// The fact guard shown as a receipt: real claims checked against a real
// CV, one removed. Static, quiet, server-rendered.

import { Check, X } from 'lucide-react';

const rows = [
  { ok: true, claim: 'Five years of bakery production', note: 'In your CV' },
  { ok: true, claim: 'Food-safety certificate', note: 'In your CV' },
  // The canonical AI fabrication: a credential that was never pursued.
  { ok: false, claim: 'PMP certification', note: 'Not in your CV. Removed.' },
];

export default function GuardReceipt() {
  return (
    <div
      role="group"
      aria-label="The draft guard checking claims against your CV"
      className="rounded-2xl border border-line bg-surface p-6 sm:p-7"
    >
      <div className="flex items-center justify-between">
        <p className="num text-[10px] uppercase tracking-[0.16em] text-low">
          Draft guard · cover letter
        </p>
        <p className="num text-[10px] uppercase tracking-[0.16em] text-ok">
          2 verified · 1 removed
        </p>
      </div>

      <ul className="mt-5 divide-y divide-line">
        {rows.map((r) => (
          <li key={r.claim} className="flex items-center justify-between gap-5 py-3.5">
            <span className="flex items-center gap-3 text-sm text-hi">
              {r.ok ? (
                <Check className="h-4 w-4 shrink-0 text-ok" aria-hidden />
              ) : (
                <X className="h-4 w-4 shrink-0 text-bad" aria-hidden />
              )}
              {r.claim}
            </span>
            <span
              className={`num shrink-0 text-[10px] uppercase tracking-[0.14em] ${
                r.ok ? 'text-ok/80' : 'text-bad/80'
              }`}
            >
              {r.note}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
