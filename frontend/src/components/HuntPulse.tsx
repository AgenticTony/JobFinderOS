'use client';

// HuntPulse — the console's signature element. The pipeline funnel as a
// living strip: hunted → matched → awaiting you → in drafts → sent.
// The numbers ARE the stats row — structure encodes the funnel. Stages
// that need the user's attention breathe amber; clicking a stage jumps
// to its view.

import { memo } from 'react';
import { Crosshair, FileText, Radar, Scale, Send } from 'lucide-react';
import type { Stats } from '@/types';
import { cn } from '@/lib/utils';
import { LiveDot } from './NextHunt';

interface Stage {
  id: string;
  label: string;
  hint: string;
  count: number;
  icon: typeof Radar;
  attention: boolean; // needs the user's decision → breathes amber
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
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      onClick={onClick}
      className={cn(
        'group flex min-w-0 flex-1 flex-col items-center gap-1.5 rounded-lg px-2 py-3 transition-colors sm:items-start sm:px-4',
        onClick && 'cursor-pointer hover:bg-surface-2'
      )}
    >
      <span className="flex items-center gap-2 self-center text-low sm:self-start">
        <stage.icon className="h-3.5 w-3.5" aria-hidden />
        <span className="text-[10px] font-medium uppercase tracking-[0.14em]">{stage.label}</span>
        {live && <span className="num text-[10px] text-signal">live</span>}
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
      </span>
      <span className="hidden truncate text-[11px] text-low sm:block">{stage.hint}</span>
    </Tag>
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
  onOpenMatches,
  onOpenApplications,
}: {
  stats: Stats | undefined;
  openDrafts: number;
  matchingRunning: boolean;
  onOpenMatches: () => void;
  onOpenApplications: () => void;
}) {
  const pending = stats?.matches_pending_decision ?? 0;
  const stages: Stage[] = [
    {
      id: 'hunted',
      label: 'Hunted',
      hint: 'jobs found so far',
      count: stats?.jobs_total ?? 0,
      icon: Radar,
      attention: false,
    },
    {
      id: 'matched',
      label: 'Matched',
      hint: 'ranked against your CV',
      count: stats?.jobs_matched ?? 0,
      icon: Crosshair,
      attention: false,
    },
    {
      id: 'awaiting',
      label: 'Awaiting you',
      hint: pending > 0 ? 'your move — approve or pass' : 'nothing to decide',
      count: pending,
      icon: Scale,
      attention: pending > 0,
    },
    {
      id: 'drafts',
      label: 'In drafts',
      hint: openDrafts > 0 ? 'review before sending' : 'no drafts waiting',
      count: openDrafts,
      icon: FileText,
      attention: openDrafts > 0,
    },
    {
      id: 'sent',
      label: 'Sent',
      hint: 'applications out the door',
      count: stats?.jobs_applied ?? 0,
      icon: Send,
      attention: false,
    },
  ];

  return (
    <section
      aria-label="Hunt pulse — your pipeline at a glance"
      className="rounded-xl border border-line bg-surface/80"
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line px-4 py-2.5 sm:px-5">
        <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-low">Hunt pulse</p>
        {matchingRunning ? (
          <p className="flex items-center gap-2 text-xs text-signal">
            <LiveDot /> AI is ranking jobs for you — new matches stream in live
          </p>
        ) : (
          <p className="text-xs text-low">
            {pending > 0
              ? `${pending} ${pending === 1 ? 'match needs' : 'matches need'} your decision`
              : openDrafts > 0
                ? `${openDrafts} ${openDrafts === 1 ? 'draft is' : 'drafts are'} ready to review`
                : 'All quiet — the next automatic hunt is on its way'}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-stretch py-1">
        {stages.map((stage, i) => (
          <div key={stage.id} className="contents">
            {i > 0 && <FlowDivider />}
            <PulseStage
              stage={stage}
              live={matchingRunning && stage.id === 'matched'}
              onClick={
                stage.id === 'awaiting'
                  ? onOpenMatches
                  : stage.id === 'drafts' || stage.id === 'sent'
                    ? onOpenApplications
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
