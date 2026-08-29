// The real match card, held still in the daylight act. The live radar
// lives in the hero scope; this is the product anatomy: score, verdict,
// actions. Server component, no motion of its own.

export default function CockpitPreview() {
  return (
    <div
      role="group"
      aria-label="Preview of a scored match in the JobFinderOS console"
      className="rounded-2xl border border-line bg-surface shadow-2xl shadow-ink/25"
    >
      <div className="p-6 sm:p-7">
        <div className="mb-5 flex items-center justify-between">
          <p className="num text-[10px] uppercase tracking-[0.16em] text-low">
            Hunt 46 · 21:00 CEST
          </p>
          <p className="num flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-signal">
            <span className="h-1.5 w-1.5 rounded-full bg-signal" aria-hidden />
            Live
          </p>
        </div>

        <h3 className="text-lg font-semibold tracking-tight text-hi">
          Baker, evening shift
        </h3>
        <p className="mt-0.5 text-sm text-mid">Pågen · Malmö · posted 2h ago</p>

        <div className="mt-6 flex items-end gap-4">
          <span className="num text-4xl font-semibold leading-none text-signal">
            87
          </span>
          <div className="flex-1 pb-0.5">
            <div className="h-1.5 overflow-hidden rounded-full bg-line-2">
              <div className="h-full w-[87%] rounded-full bg-signal" />
            </div>
            <p className="num mt-1.5 text-[10px] uppercase tracking-[0.16em] text-low">
              Match score · strong
            </p>
          </div>
        </div>

        <p className="num mt-6 text-[10px] uppercase tracking-[0.16em] text-low">
          Verdict
        </p>
        <p className="mt-1.5 text-sm leading-relaxed text-mid">
          Five years of bakery production and a food-safety certificate. Direct
          employer ad; the agency cross-post was merged automatically.
        </p>

        {/* Preview only: styled spans, never focusable. */}
        <div className="mt-6 flex gap-2" aria-hidden>
          <span className="rounded-full bg-signal px-5 py-2 text-[13px] font-semibold text-ink">
            Tailor CV
          </span>
          <span className="rounded-full border border-line-2 px-5 py-2 text-[13px] font-medium text-mid">
            Skip
          </span>
        </div>
      </div>
    </div>
  );
}
