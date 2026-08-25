'use client';

// NextHunt — the live countdown to the next scheduled pipeline run.
// The ticking seconds are aria-hidden (a per-second live region would be
// obnoxious); screen readers get the static absolute time instead.

import { useEffect, useMemo, useState } from 'react';

function formatRemaining(ms: number): string {
  if (ms <= 0) return 'any moment…';
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

export function useCountdown(nextRunAt: string | null): string | null {
  const target = useMemo(() => (nextRunAt ? new Date(nextRunAt).getTime() : null), [nextRunAt]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (target === null) return;
    const tick = () => setNow(Date.now());
    const id = setInterval(tick, 1000);
    tick();
    return () => clearInterval(id);
  }, [target]);

  if (target === null) return null;
  return formatRemaining(target - now);
}

// Static label for screen readers and the compact sidebar/mobile variants
export function useNextRunLabel(
  nextRunAt: string | null,
  schedulerEnabled: boolean,
  intervalMinutes: number
): string {
  const countdown = useCountdown(schedulerEnabled ? nextRunAt : null);
  if (!schedulerEnabled) return 'manual hunts only';
  if (!nextRunAt) return `every ${intervalMinutes}m`;
  return `next hunt in ${countdown}`;
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
  const countdown = useCountdown(schedulerEnabled ? nextRunAt : null);
  const at = nextRunAt ? new Date(nextRunAt) : null;

  if (!schedulerEnabled) {
    return (
      <p className="mt-1 flex items-center gap-2 text-xs text-mid">
        <span className="h-2 w-2 rounded-full bg-line-2" aria-hidden />
        Automatic hunts off
      </p>
    );
  }

  return (
    <p className="mt-1 flex items-center gap-2 text-xs text-mid">
      <LiveDot />
      <span>
        <span className="num text-hi" aria-hidden>
          {countdown ?? `every ${intervalMinutes}m`}
        </span>
        <span className="sr-only">
          {at
            ? `next automatic hunt at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
            : `hunts every ${intervalMinutes} minutes`}
        </span>
      </span>
      <span className="hidden text-low lg:inline">to next hunt</span>
    </p>
  );
}

export { LiveDot };
