'use client';

// HuntPulse — the console's signature element. The pipeline funnel as a
// living strip: hunted → matched → awaiting you → in drafts → sent.
// The numbers ARE the stats row — structure encodes the funnel. Stages
// that need the user's attention breathe amber; clicking a stage jumps
// to its view.

import { memo } from 'react';
import { Crosshair, FileText, Radar, Scale, Send } from 'lucide-react';
import type { Stats } from '@/types';
import { cn, parseUtcDate, timeAgo } from '@/lib/utils';
import { clockTime, LiveDot, useCountdown } from './NextHunt';
import { useLocale, useT } from '@/i18n/LocaleContext';

interface Stage {
  id: string;
  label: string;
  hint: string;
  count: number;
  icon: typeof Radar;
  attention: boolean; // needs the user's decision → breathes amber
  delta?: number; // "+N" fresh-count chip (last hunt)
}

function PulseStage({
  stage,
  live,
  onClick,
}: {
  stage: Stage;
  live?: boolean;
  onClick?: () => void;
}) {
  const t = useT().console.pulse;
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      onClick={onClick}
      className={cn(
        'group flex min-w-0 flex-1 flex-col items-center gap-1.5 rounded-lg px-2 py-3 transition-colors sm:items-start sm:px-4',
        onClick && 'cursor-pointer hover:bg-surface-2'
      )}
    >
      <span className="flex items-center gap-2 self-center text-mid sm:self-start">
        <stage.icon className="h-3.5 w-3.5" aria-hidden />
        <span className="text-[10px] font-medium uppercase tracking-[0.14em]">{stage.label}</span>
        {live && <span className="num text-[10px] text-signal">{t.live}</span>}
      </span>
      <span className="flex items-center gap-2 self-center sm:self-start">
        {stage.attention && <LiveDot />}
        <span
          className={cn(
            'num text-2xl font-semibold tracking-tight',
            stage.attention ? 'text-signal' : 'text-hi'
          )}
        >
          {stage.count}
        </span>
        {stage.delta ? (
          <span className="num rounded bg-ok/10 px-1 py-0.5 text-[11px] text-ok">+{stage.delta}</span>
        ) : null}
      </span>
      <span className="hidden truncate text-[11px] text-mid sm:block">{stage.hint}</span>
    </Tag>
  );
}

// Header-right schedule block: last hunt + live countdown (replaces the
// old Automatic hunts card)
function PulseSchedule({
  nextRunAt,
  schedulerEnabled,
  intervalMinutes,
  lastHuntAt,
}: {
  nextRunAt: string | null;
  schedulerEnabled: boolean;
  intervalMinutes: number;
  lastHuntAt: string | null;
}) {
  const { locale } = useLocale();
  const tc = useT().console;
  const t = tc.pulse;
  const tn = tc.nextHunt;
  const countdown = useCountdown(schedulerEnabled ? nextRunAt : null);
  const at = nextRunAt ? parseUtcDate(nextRunAt) : null;
  const overdue =
    lastHuntAt !== null &&
    Date.now() - parseUtcDate(lastHuntAt).getTime() > intervalMinutes * 60_000 * 1.5;

  if (!schedulerEnabled) {
    return <p className="text-xs text-low">{tn.manualOnly}</p>;
  }

  return (
    <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs">
      {lastHuntAt && (
        <span className="text-low">
          {t.lastHunt(timeAgo(lastHuntAt, tc.time))}
          {overdue && (
            <span className="ml-1.5 rounded bg-signal/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-signal">
              {t.overdue}
            </span>
          )}
        </span>
      )}
      <span className="flex items-center gap-2 text-mid">
        <LiveDot />
        <span>
          <span aria-hidden>{t.nextHuntIn} </span>
          <span className="num text-hi" aria-hidden>
            {countdown ?? tn.every(intervalMinutes)}
          </span>
          <span className="sr-only">
            {at ? tn.nextAt(clockTime(at, locale)) : tn.huntsEvery(intervalMinutes)}
          </span>
        </span>
      </span>
    </p>
  );
}

const FlowDivider = memo(function FlowDivider() {
  return (
    <div className="relative hidden h-px w-4 shrink-0 self-center bg-line sm:block" aria-hidden>
      <span className="absolute -right-[3px] -top-[3px] h-[5px] w-[5px] rotate-45 border-r border-t border-line-2" />
    </div>
  );
});

function HuntPulseBase({
  stats,
  openDrafts,
  matchingRunning,
  nextRunAt,
  schedulerEnabled,
  intervalMinutes,
  lastHuntAt,
  onOpenMatches,
  onOpenReview,
  onOpenSent,
}: {
  stats: Stats | undefined;
  openDrafts: number;
  matchingRunning: boolean;
  nextRunAt: string | null;
  schedulerEnabled: boolean;
  intervalMinutes: number;
  lastHuntAt: string | null;
  onOpenMatches: () => void;
  onOpenReview: () => void;
  onOpenSent: () => void;
}) {
  const t = useT().console.pulse;
  const pending = stats?.matches_pending_decision ?? 0;
  const last24 = stats?.jobs_last_24h ?? 0;
  const stages: Stage[] = [
    {
      id: 'hunted',
      label: t.hunted,
      hint: last24 ? t.huntedHint(last24) : t.huntedHintZero,
      count: stats?.jobs_total ?? 0,
      icon: Radar,
      attention: false,
      delta: last24,
    },
    {
      id: 'matched',
      label: t.matched,
      hint: t.matchedHint,
      count: stats?.jobs_matched ?? 0,
      icon: Crosshair,
      attention: false,
    },
    {
      id: 'awaiting',
      label: t.awaiting,
      hint: pending > 0 ? t.awaitingHint : t.awaitingHintZero,
      count: pending,
      icon: Scale,
      attention: pending > 0,
    },
    {
      id: 'drafts',
      label: t.drafts,
      hint: openDrafts > 0 ? t.draftsHint : t.draftsHintZero,
      count: openDrafts,
      icon: FileText,
      attention: openDrafts > 0,
    },
    {
      id: 'sent',
      label: t.sent,
      hint: t.sentHint,
      count: stats?.jobs_applied ?? 0,
      icon: Send,
      attention: false,
    },
  ];

  return (
    <section
      aria-label={t.aria}
      className="rounded-xl border border-line bg-surface/80"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-hi">{t.title}</h2>
          {matchingRunning ? (
            <p className="flex items-center gap-2 text-xs text-signal">
              <LiveDot /> {t.aiRanking}
            </p>
          ) : (
            <p className="text-xs text-mid">
              {pending > 0
                ? t.needDecision(pending)
                : openDrafts > 0
                  ? t.draftsReady(openDrafts)
                  : t.allQuiet}
            </p>
          )}
        </div>
        <PulseSchedule
          nextRunAt={nextRunAt}
          schedulerEnabled={schedulerEnabled}
          intervalMinutes={intervalMinutes}
          lastHuntAt={lastHuntAt}
        />
      </div>
      {/* Phones: five stages cannot fit one 360px row (the uppercase
          labels + icons alone need ~85px each) — wrap the funnel into a
          3-column grid. sm+: the single flex strip with flow arrows. */}
      <div className="grid grid-cols-3 py-1 sm:flex sm:flex-wrap sm:items-stretch">
        {stages.map((stage, i) => (
          <div key={stage.id} className="contents">
            {i > 0 && <FlowDivider />}
            <PulseStage
              stage={stage}
              live={matchingRunning && stage.id === 'matched'}
              onClick={
                stage.id === 'awaiting'
                  ? onOpenMatches
                  : stage.id === 'drafts'
                    ? onOpenReview
                    : stage.id === 'sent'
                      ? onOpenSent
                      : undefined
              }
            />
          </div>
        ))}
      </div>
    </section>
  );
}

export default memo(HuntPulseBase);
