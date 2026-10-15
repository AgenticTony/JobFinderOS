// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same guard block — the honesty stack is the wedge, and every
// surface we publish should carry it.

import { ShieldCheck } from 'lucide-react';

import GuardReceipt from '@/components/landing/GuardReceipt';
import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

export default function GuardSection({ t = dicts.en }: { t?: Dict }) {
  return (
    <div className="mx-auto max-w-6xl px-6 py-28">
      <div className="grid items-center gap-14 lg:grid-cols-2 lg:gap-20">
        <Reveal>
          <ShieldCheck className="h-8 w-8 text-ok" aria-hidden />
          <h2 className="mt-6 font-display text-[clamp(2.25rem,4.5vw,3.5rem)] font-semibold leading-[1.06] tracking-[-0.02em] text-paper">
            {t.guardSec.h2a}
            <br />
            {t.guardSec.h2b}
          </h2>
          <p className="mt-5 max-w-md text-lg leading-relaxed text-paper/60">
            {t.guardSec.body}
          </p>
        </Reveal>
        <Reveal delay={120}>
          <GuardReceipt strings={t.guardSec.receipt} />
        </Reveal>
      </div>
    </div>
  );
}
