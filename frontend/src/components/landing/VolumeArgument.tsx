// Extracted from LandingView (WO-28) so persona pages (WO-29) compose
// the same anti-volume argument.
//
// CLAIMS GATE — the "hiring managers" statistic. Two unverified and
// mutually inconsistent versions circulated ("two thirds" in the body
// copy, "65%" in the old stat line, both attributed to "Forbes, March
// 2026" — a citation nobody checked). WO-28's rule: the numeric claim
// is OFF the page until the owner pastes a verified citation into
// VOLUME_STAT_CITATION below. The claim text is ready in both
// dictionaries (volume.statClaim); filling in this one constant turns
// the line on in both languages. This IS the Forbes placeholder.

import Reveal from '@/components/landing/Reveal';
import { dicts, type Dict } from '@/i18n/dict';

// TODO(owner): paste the checked citation (publication, article title,
// date) and the stat renders under the argument in both languages.
// Until then: null = the paragraph stays off, by design.
const VOLUME_STAT_CITATION: string | null = null;

export default function VolumeArgument({ t = dicts.en }: { t?: Dict }) {
  return (
    <div className="border-t border-line">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
        <Reveal>
          <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
            {t.volume.h2}
          </h2>
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-paper/70">
            {t.volume.body}
          </p>
          {VOLUME_STAT_CITATION && (
            <p className="num mt-8 text-xs uppercase tracking-[0.14em] text-paper/45">
              {t.volume.statClaim} {VOLUME_STAT_CITATION}
            </p>
          )}
        </Reveal>
      </div>
    </div>
  );
}
