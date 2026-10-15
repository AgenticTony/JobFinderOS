// WO-28: sticky mobile CTA. The landing is long and its only CTAs sit
// in the fixed header and at the very bottom — on phones the header
// button is small and the final CTA is a full scroll away. This bar
// keeps one way in on screen at all times on small viewports; the
// spacer rendered beside it in LandingView stops it covering the
// footer's last row.

import Link from 'next/link';

import { dicts, type Dict } from '@/i18n/dict';

export default function StickyCta({ t = dicts.en }: { t?: Dict }) {
  const s = t.sticky;

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-ink/90 backdrop-blur-xl sm:hidden">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
        <p className="num text-[10px] uppercase tracking-[0.14em] text-paper/50">
          {s.note}
        </p>
        <Link
          href="/app"
          className="rounded-full bg-signal px-5 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
        >
          {s.cta}
        </Link>
      </div>
    </div>
  );
}
