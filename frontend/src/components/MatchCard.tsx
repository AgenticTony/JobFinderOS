'use client';

// Match card — expandable recommendation with the approval workflow.
// After approval, the user prepares a tailored application (draft stage);
// cover notes are no longer shown here — they're generated per-application
// in the Applications view instead.

import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowRight,
  Briefcase,
  Check,
  ChevronDown,
  ExternalLink,
  FileText,
  Sparkles,
  X,
} from 'lucide-react';
import type { Match } from '@/types';
import ScoreRing from './ScoreRing';
import TierBadge from './TierBadge';
import AdzunaAttribution from './AdzunaAttribution';
import { cn, timeAgo } from '@/lib/utils';

interface Props {
  match: Match;
  onDecision: (matchId: number, decision: 'approved' | 'rejected') => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  onReview?: () => void; // jump to the draft review (approved + already drafted)
  prepared?: boolean;
}

export default function MatchCard({ match, onDecision, onPrepare, onReview, prepared }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // Rolling 24h "new" — decays per-card instead of everything going stale at
  // midnight; recomputed on every data refresh.
  const isNew = Date.now() - new Date(match.created_at).getTime() < 24 * 60 * 60 * 1000;
  // Posting age (when the board publishes it) — old postings get a red hint
  const postedMs = match.job?.published_at ? Date.now() - new Date(match.job.published_at).getTime() : null;
  const postedAgeDays = postedMs !== null ? Math.floor(postedMs / 86_400_000) : null;
  const postedStale = postedAgeDays !== null && postedAgeDays >= 21;

  const handleDecision = async (decision: 'approved' | 'rejected') => {
    setBusy(decision);
    try {
      await onDecision(match.id, decision);
    } finally {
      setBusy(null);
    }
  };

  const handlePrepare = async () => {
    setBusy('prepare');
    try {
      await onPrepare(match.job_id);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface/80 transition-colors hover:border-line-2">
      {/* Header row — job-board convention: company tile left, score right */}
      <div className="flex cursor-pointer items-center gap-3.5 p-4" onClick={() => setExpanded(!expanded)}>
        <span
          className="num flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-line bg-surface-2 text-sm font-semibold text-mid"
          aria-hidden
        >
          {match.job?.company?.trim()?.[0]?.toUpperCase() ?? <Briefcase className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-semibold text-hi">{match.job?.title ?? 'Unknown job'}</h3>
            <TierBadge tier={match.tier} />
            {match.recommendation === 'apply' && (
              <span className="inline-flex items-center gap-1 rounded-full bg-signal/15 px-2 py-0.5 text-xs font-medium text-signal">
                <Sparkles className="h-3 w-3" /> AI says: apply
              </span>
            )}
            {match.decision === 'approved' && (
              <span className="rounded-full bg-ok/15 px-2 py-0.5 text-xs text-ok">approved</span>
            )}
            {match.decision === 'rejected' && (
              <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-mid">rejected</span>
            )}
            {prepared && (
              <span className="rounded-full bg-info/15 px-2 py-0.5 text-xs text-info">
                <FileText className="mr-1 inline h-3 w-3" />
                application drafted
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-sm text-mid">
            {[match.job?.company, match.job?.location, match.job?.remote ? 'Remote' : null, match.job?.source]
              .filter(Boolean)
              .join(' · ')}
            {match.job?.source === 'adzuna' && <AdzunaAttribution />}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-low">
            {match.job?.salary && (
              <span className="num rounded border border-line bg-surface-2 px-1.5 py-0.5">
                {match.job.salary}
              </span>
            )}
            {isNew && (
              <span className="rounded bg-signal/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-signal">
                new
              </span>
            )}
            <span>matched {timeAgo(match.created_at)}</span>
            {postedAgeDays !== null && (
              <span className={cn('num rounded px-1.5 py-0.5', postedStale ? 'bg-bad/10 text-bad' : '')}>
                posted {postedAgeDays === 0 ? 'today' : `${postedAgeDays}d ago`}
              </span>
            )}
          </p>
        </div>
        <ScoreRing score={match.score} />
        <ChevronDown
          className={cn('h-5 w-5 shrink-0 text-low transition-transform', expanded && 'rotate-180')}
        />
      </div>

      {/* Expanded detail */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-line"
          >
            <div className="space-y-4 p-4">
              {match.reasoning && (
                <p className="text-sm leading-relaxed text-mid">{match.reasoning}</p>
              )}

              <div className="grid gap-3 sm:grid-cols-3">
                <SkillChips label="You have" skills={match.matched_skills} tone="ok" />
                <SkillChips label="They want (gaps)" skills={match.missing_skills} tone="bad" />
                <SkillChips label="Transferable" skills={match.transferable_skills} tone="info" />
              </div>

              {/* Actions */}
              <div className="flex flex-wrap items-center gap-2">
                {!match.decision && (
                  <>
                    <button
                      onClick={() => handleDecision('approved')}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
                    >
                      <Check className="h-4 w-4" /> Approve
                    </button>
                    <button
                      onClick={() => handleDecision('rejected')}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3.5 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi disabled:opacity-50"
                    >
                      <X className="h-4 w-4" /> Pass
                    </button>
                  </>
                )}

                {match.decision === 'approved' && !prepared && (
                  <button
                    onClick={handlePrepare}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
                  >
                    <FileText className="h-4 w-4" />
                    {busy === 'prepare' ? 'Tailoring your CV & cover letter…' : 'Prepare application'}
                  </button>
                )}

                {match.decision === 'approved' && prepared && onReview && (
                  <button
                    onClick={onReview}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98]"
                  >
                    Review & send <ArrowRight className="h-4 w-4" />
                  </button>
                )}

                {match.job?.url && (
                  <a
                    href={match.job.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-low transition-colors hover:text-mid"
                  >
                    <ExternalLink className="h-4 w-4" /> View posting
                  </a>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SkillChips({ label, skills, tone }: { label: string; skills: string[]; tone: 'ok' | 'bad' | 'info' }) {
  const tones = {
    ok: 'bg-ok/10 text-ok border-ok/20',
    bad: 'bg-bad/10 text-bad border-bad/20',
    info: 'bg-info/10 text-info border-info/20',
  };
  return (
    <div>
      <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-low">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {skills.length ? (
          skills.slice(0, 8).map((s) => (
            <span key={s} className={cn('rounded-md border px-2 py-0.5 text-xs', tones[tone])}>
              {s}
            </span>
          ))
        ) : (
          <span className="text-xs text-low">—</span>
        )}
      </div>
    </div>
  );
}
