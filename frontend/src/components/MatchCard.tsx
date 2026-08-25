'use client';

// Match card — expandable recommendation with the approval workflow.
// After approval, the user prepares a tailored application (draft stage);
// cover notes are no longer shown here — they're generated per-application
// in the Applications tab instead.

import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Check, ChevronDown, ExternalLink, FileText, Sparkles, X } from 'lucide-react';
import type { Match } from '@/types';
import ScoreRing from './ScoreRing';
import TierBadge from './TierBadge';
import AdzunaAttribution from './AdzunaAttribution';
import { cn, timeAgo } from '@/lib/utils';

interface Props {
  match: Match;
  onDecision: (matchId: number, decision: 'approved' | 'rejected') => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  prepared?: boolean;
}

export default function MatchCard({ match, onDecision, onPrepare, prepared }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

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
    <div className="rounded-xl border border-white/10 bg-white/[0.03] transition-colors hover:border-white/20">
      {/* Header row */}
      <div className="flex cursor-pointer items-center gap-4 p-4" onClick={() => setExpanded(!expanded)}>
        <ScoreRing score={match.score} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-semibold text-zinc-100">{match.job?.title ?? 'Unknown job'}</h3>
            <TierBadge tier={match.tier} />
            {match.recommendation === 'apply' && (
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-500/15 px-2 py-0.5 text-xs font-medium text-violet-300">
                <Sparkles className="h-3 w-3" /> AI says: apply
              </span>
            )}
            {match.decision === 'approved' && (
              <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">approved</span>
            )}
            {match.decision === 'rejected' && (
              <span className="rounded-full bg-zinc-500/15 px-2 py-0.5 text-xs text-zinc-400">rejected</span>
            )}
            {prepared && (
              <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-xs text-sky-400">
                <FileText className="mr-1 inline h-3 w-3" />
                application drafted
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-sm text-zinc-400">
            {[match.job?.company, match.job?.location, match.job?.remote ? 'Remote' : null, match.job?.source]
              .filter(Boolean)
              .join(' · ')}
            {match.job?.source === 'adzuna' && <AdzunaAttribution />}
          </p>
        </div>
        <ChevronDown
          className={cn('h-5 w-5 shrink-0 text-zinc-500 transition-transform', expanded && 'rotate-180')}
        />
      </div>

      {/* Expanded detail */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-white/10"
          >
            <div className="space-y-4 p-4">
              {match.reasoning && (
                <p className="text-sm leading-relaxed text-zinc-300">{match.reasoning}</p>
              )}

              <div className="grid gap-3 sm:grid-cols-3">
                <SkillChips label="You have" skills={match.matched_skills} tone="emerald" />
                <SkillChips label="They want (gaps)" skills={match.missing_skills} tone="rose" />
                <SkillChips label="Transferable" skills={match.transferable_skills} tone="violet" />
              </div>

              {/* Actions */}
              <div className="flex flex-wrap items-center gap-2">
                {!match.decision && (
                  <>
                    <button
                      onClick={() => handleDecision('approved')}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
                    >
                      <Check className="h-4 w-4" /> Approve
                    </button>
                    <button
                      onClick={() => handleDecision('rejected')}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3.5 py-2 text-sm text-zinc-300 transition hover:bg-white/5 disabled:opacity-50"
                    >
                      <X className="h-4 w-4" /> Reject
                    </button>
                  </>
                )}

                {match.decision === 'approved' && !prepared && (
                  <button
                    onClick={handlePrepare}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3.5 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
                  >
                    <FileText className="h-4 w-4" />
                    {busy === 'prepare' ? 'Tailoring your CV & cover letter…' : 'Prepare application'}
                  </button>
                )}

                {match.job?.url && (
                  <a
                    href={match.job.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-zinc-400 transition hover:text-zinc-200"
                  >
                    <ExternalLink className="h-4 w-4" /> View posting
                  </a>
                )}

                <span className="ml-auto text-xs text-zinc-600">
                  matched {timeAgo(match.created_at)}
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SkillChips({ label, skills, tone }: { label: string; skills: string[]; tone: 'emerald' | 'rose' | 'violet' }) {
  const tones = {
    emerald: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
    rose: 'bg-rose-500/10 text-rose-300 border-rose-500/20',
    violet: 'bg-violet-500/10 text-violet-300 border-violet-500/20',
  };
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {skills.length ? (
          skills.slice(0, 8).map((s) => (
            <span key={s} className={cn('rounded-md border px-2 py-0.5 text-xs', tones[tone])}>
              {s}
            </span>
          ))
        ) : (
          <span className="text-xs text-zinc-600">—</span>
        )}
      </div>
    </div>
  );
}
