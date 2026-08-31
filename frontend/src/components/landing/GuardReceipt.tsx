// FE-11: this receipt is an ILLUSTRATIVE EXAMPLE, not a real audit — the
// rows below are hardcoded sample claims, and it must be impossible to
// mistake the card for a real verification artifact. The MECHANISM it
// illustrates is real and runs on every draft (the two-layer fabrication
// guard: deterministic claim checks, then the Z.ai judge; findings
// trigger a regeneration with the claim named, and a surviving claim
// BLOCKS the draft — backend/app/services/draft_service.py:120-215). The
// example's wording mirrors that real behaviour: an unsupported claim is
// rejected and the draft regenerated, never silently "removed".

import { Check, X } from 'lucide-react';

const rows = [
  { ok: true, claim: 'Five years of bakery production', note: 'In your CV' },
  { ok: true, claim: 'Food-safety certificate', note: 'In your CV' },
  // The canonical AI fabrication: a credential that was never pursued.
  { ok: false, claim: 'PMP certification', note: 'Not in your CV — draft regenerated' },
];

export default function GuardReceipt() {
  return (
    <div
      role="group"
      aria-label="Example of the draft guard checking claims against your CV (illustrative, not a real audit)"
      className="rounded-2xl border border-line bg-surface p-6 sm:p-7"
    >
      <div className="flex items-center justify-between gap-3">
        <p className="num flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-low">
          Draft guard · cover letter
          {/* The honesty label: a badge, not fine print. */}
          <span className="rounded border border-paper/25 px-1.5 py-0.5 text-paper/60">
            Example
          </span>
        </p>
        <p className="num text-[10px] uppercase tracking-[0.16em] text-ok">
          2 verified · 1 rejected
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

      {/* FE-11: the caption that keeps the card honest — sample data,
          clearly labelled, with the real guarantee stated as mechanism. */}
      <p className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-paper/45">
        Illustrative example with sample claims — not a real audit. Every real
        draft gets this check before you see it: a claim your CV cannot back is
        rejected and the draft regenerated; one that survives blocks the draft.
      </p>
    </div>
  );
}
