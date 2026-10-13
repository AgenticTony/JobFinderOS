'use client';

// NextHunt — the live countdown to the next scheduled pipeline run.
// The ticking seconds are aria-hidden (a per-second live region would be
// obnoxious); screen readers get the static absolute time instead.

import { useEffect, useMemo, useState } from 'react';

import { parseUtcDate } from '@/lib/utils';
import type { ConsoleDict } from '@/i18n/console';
import { useLocale, useT } from '@/i18n/LocaleContext';

function formatRemaining(ms: number, t: ConsoleDict['nextHunt']): string {
  if (ms <= 0) return t.anyMoment;
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return t.hours(h, String(m).padStart(2, '0'));
  if (m > 0) return t.minutes(m, String(sec).padStart(2, '0'));
  return t.seconds(sec);
}

// Clock time in the console's language (24h in Swedish), not the
// browser's default locale — the sentence around it is translated too.
export function clockTime(d: Date, locale: string): string {
  return d.toLocaleTimeString(locale === 'sv' ? 'sv-SE' : 'en-GB', { hour: '2-digit', minute: '2-digit' });
}

export function useCountdown(nextRunAt: string | null): string | null {
  const t = useT().console.nextHunt;
  const target = useMemo(
    () => (nextRunAt ? parseUtcDate(nextRunAt).getTime() : null),
    [nextRunAt]
  );
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (target === null) return;
    const tick = () => setNow(Date.now());
    const id = setInterval(tick, 1000);
    tick();
    return () => clearInterval(id);
  }, [target]);

  if (target === null) return null;
  return formatRemaining(target - now, t);
}

// Static label for screen readers and the compact sidebar/mobile variants
export function useNextRunLabel(
  nextRunAt: string | null,
  schedulerEnabled: boolean,
  intervalMinutes: number
): string {
  const t = useT().console.nextHunt;
  const countdown = useCountdown(schedulerEnabled ? nextRunAt : null);
  if (!schedulerEnabled) return t.manualOnlyLower;
  if (!nextRunAt) return t.every(intervalMinutes);
  return t.nextIn(countdown ?? '');
}

// The breathing dot — isolated so its animation never re-renders parents.
function LiveDot({ className }: { className?: string }) {
  return (
    <span className={`relative inline-flex h-2 w-2 ${className ?? ''}`} aria-hidden>
      <span className="absolute inset-0 animate-ping rounded-full bg-signal opacity-60 [animation-duration:2s]" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
    </span>
  );
}

export default function NextHunt({
  nextRunAt,
  schedulerEnabled,
  intervalMinutes,
}: {
  nextRunAt: string | null;
  schedulerEnabled: boolean;
  intervalMinutes: number;
}) {
  const { locale } = useLocale();
  const t = useT().console.nextHunt;
  const countdown = useCountdown(schedulerEnabled ? nextRunAt : null);
  const at = nextRunAt ? parseUtcDate(nextRunAt) : null;

  if (!schedulerEnabled) {
    return (
      <p className="mt-1 flex items-center gap-2 text-xs text-mid">
        <span className="h-2 w-2 rounded-full bg-line-2" aria-hidden />
        {t.manualOnly}
      </p>
    );
  }

  return (
    <p className="mt-1 flex items-center gap-2 text-xs text-mid">
      <LiveDot />
      <span>
        <span className="num text-hi" aria-hidden>
          {countdown ?? t.every(intervalMinutes)}
        </span>
        <span className="sr-only">
          {at ? t.nextAt(clockTime(at, locale)) : t.huntsEvery(intervalMinutes)}
        </span>
      </span>
      <span className="hidden text-mid lg:inline">{t.toNext}</span>
    </p>
  );
}

export { LiveDot };
