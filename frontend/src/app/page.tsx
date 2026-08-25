'use client';

// JobFinderOS — the hunting console.
// Pipeline: scrape -> match vs CV -> approve -> tailor CV & cover letter -> review -> send.
// Shell: left sidebar (nav + live hunt status) on md+, compact top bar on mobile.
// Landing view is the Dashboard: Hunt Pulse funnel + next decisions + status rail.

import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  AlertTriangle,
  Copy,
  Cpu,
  Download,
  ExternalLink,
  FileText,
  Inbox,
  Loader2,
  Mail,
  MousePointerClick,
  Play,
  Radar,
  Save,
  Send,
} from 'lucide-react';
import CvUpload from '@/components/CvUpload';
import MatchCard from '@/components/MatchCard';
import OnboardingWizard from '@/components/OnboardingWizard';
import Sidebar, { NAV, type View } from '@/components/Sidebar';
import HuntPulse from '@/components/HuntPulse';
import NextHunt from '@/components/NextHunt';
import {
  decideMatch,
  draftCoverLetterPdfUrl,
  draftCvPdfUrl,
  getApplications,
  getDrafts,
  getMatches,
  getPipelineStatus,
  getProfile,
  getProfileStatus,
  prepareDraft,
  retryApplication,
  runMatching,
  runPipeline,
  saveOnboarding,
  submitDraft,
  updateDraft,
  updateProfile,
  uploadCv,
} from '@/lib/api';
import type {
  Application,
  ApplicationDraft,
  Match,
  OnboardingPayload,
  PipelineRunResponse,
  PipelineStatusResponse,
  Profile,
  ProfileStatus,
} from '@/types';
import { cn, timeAgo } from '@/lib/utils';

export default function Home() {
  const [view, setView] = useState<View>('dashboard');
  const [status, setStatus] = useState<ProfileStatus | null>(null);
  const [pipeStatus, setPipeStatus] = useState<PipelineStatusResponse | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [matches, setMatches] = useState<Match[]>([]);
  const [drafts, setDrafts] = useState<ApplicationDraft[]>([]);
  const [applications, setApplications] = useState<(Application & { job?: { title: string; company: string | null } })[]>([]);
  const [matchesFilter, setMatchesFilter] = useState<'pending' | 'all' | 'approved'>('pending');
  const [pipelineBusy, setPipelineBusy] = useState(false);
  const [matchPolling, setMatchPolling] = useState(false);
  const [pipelineResult, setPipelineResult] = useState<PipelineRunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);

  const refresh = useCallback(async () => {
    const [statusRes, profileRes, pipeRes] = await Promise.allSettled([
      getProfileStatus(),
      getProfile(),
      getPipelineStatus(),
    ]);
    if (statusRes.status === 'fulfilled') setStatus(statusRes.value);
    if (profileRes.status === 'fulfilled') setProfile(profileRes.value);
    if (pipeRes.status === 'fulfilled') setPipeStatus(pipeRes.value);
    const [matchRes, draftRes, appsRes] = await Promise.all([
      getMatches({ limit: 200 }).catch(() => []),
      getDrafts().catch(() => []),
      getApplications().catch(() => []),
    ]);
    setMatches(matchRes);
    setDrafts(draftRes);
    setApplications(appsRes);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Keep the countdown and source health fresh without hammering the API
  useEffect(() => {
    const id = setInterval(() => {
      getPipelineStatus().then(setPipeStatus).catch(() => {});
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  // While background matching runs, poll status + stream fresh matches in
  useEffect(() => {
    if (!matchPolling) return;
    const poll = setInterval(async () => {
      try {
        const [st, ms] = await Promise.all([
          getPipelineStatus(),
          getMatches({ limit: 200 }),
        ]);
        setPipeStatus(st);
        setStatus((prev) => (prev ? { ...prev, stats: st.stats } : prev));
        setMatches(ms);
        if (!st.matching_running) {
          setMatchPolling(false);
          refresh();
        }
      } catch {
        // keep polling; transient errors are fine
      }
    }, 8000);
    return () => clearInterval(poll);
  }, [matchPolling, refresh]);

  const handleRunPipeline = async () => {
    setPipelineBusy(true);
    setPipelineResult(null);
    try {
      // 1) Scrape only — fast (~10s), returns immediately with results
      const result = await runPipeline({ match: false });
      setPipelineResult(result);
      // 2) Kick AI matching into the background and stream results via polling
      await runMatching();
      setMatchPolling(true);
      await refresh();
    } catch (err) {
      setPipelineResult({
        scrape: [],
        match: {
          status: 'failed',
          jobs_considered: 0,
          matches_created: 0,
          error: err instanceof Error ? err.message : 'Pipeline failed',
        },
        top_matches: [],
      });
    } finally {
      setPipelineBusy(false);
    }
  };

  const handleUpload = async (file: File) => {
    await uploadCv(file);
    await refresh();
  };

  // Show the onboarding wizard once a CV exists but setup hasn't been done
  useEffect(() => {
    if (profile && !profile.onboarded) setShowWizard(true);
  }, [profile?.onboarded]);

  const handleOnboardingComplete = async (payload: OnboardingPayload) => {
    await saveOnboarding(payload);
    setShowWizard(false);
    await refresh();
    handleRunPipeline(); // first targeted run, straight away
  };

  const handleDecision = async (matchId: number, decision: 'approved' | 'rejected') => {
    await decideMatch(matchId, decision);
    await refresh();
  };

  const handlePrepare = async (jobId: number) => {
    await prepareDraft(jobId); // tailors CV + cover letter (~5-20s)
    setView('applications'); // take the user straight to the review stage
    await refresh();
  };

  const preparedJobIds = new Set(
    drafts.filter((d) => d.status === 'ready' || d.status === 'submitted').map((d) => d.job_id)
  );

  const filteredMatches = matches.filter((m) => {
    if (matchesFilter === 'pending') return !m.decision;
    if (matchesFilter === 'approved') return m.decision === 'approved';
    return true;
  });

  const stats = pipeStatus?.stats ?? status?.stats;
  const openDrafts = drafts.filter((d) => d.status !== 'submitted').length;
  const hunting = pipelineBusy || matchPolling;
  const pendingCount = stats?.matches_pending_decision ?? 0;

  const huntButton = (compact: boolean) => (
    <button
      onClick={handleRunPipeline}
      disabled={hunting}
      aria-busy={hunting}
      title="Scrape all sources and rank new jobs against your CV"
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg bg-signal font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-60',
        compact ? 'h-9 w-9 shrink-0' : 'w-full px-3 py-2.5 text-sm'
      )}
    >
      {hunting ? (
        <Loader2 className={cn('animate-spin', compact ? 'h-4 w-4' : 'h-4 w-4')} />
      ) : (
        <Play className={cn(compact ? 'h-4 w-4' : 'h-4 w-4')} aria-hidden />
      )}
      {!compact && (
        <span className="hidden lg:inline">
          {pipelineBusy ? 'Hunting…' : matchPolling ? 'Matching in background…' : 'Hunt now'}
        </span>
      )}
      {compact && <span className="sr-only">Hunt now — run the pipeline</span>}
    </button>
  );

  return (
    <div className="console-backdrop min-h-dvh bg-ink text-hi">
      {/* Mobile top bar (sidebar takes over from md) */}
      <header className="sticky top-0 z-40 border-b border-line bg-ink/85 backdrop-blur md:hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2.5">
            <Radar className="h-5 w-5 text-signal" aria-hidden />
            <span className="font-semibold tracking-tight">JobFinderOS</span>
          </div>
          {huntButton(true)}
        </div>
        <nav className="flex border-t border-line" aria-label="Main">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setView(id)}
              aria-current={view === id ? 'page' : undefined}
              className={cn(
                'relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium',
                view === id ? 'text-signal' : 'text-low hover:text-mid'
              )}
            >
              {view === id && <span className="absolute inset-x-4 top-0 h-0.5 rounded-full bg-signal" />}
              <Icon className="h-[18px] w-[18px]" aria-hidden />
              {label}
              {id === 'matches' && pendingCount > 0 && (
                <span className="num absolute right-3 top-1.5 rounded-full bg-signal/15 px-1.5 text-[10px] text-signal">
                  {pendingCount}
                </span>
              )}
            </button>
          ))}
        </nav>
      </header>

      <div className="md:flex">
        <Sidebar
          view={view}
          onNavigate={setView}
          pendingCount={pendingCount}
          nextRunAt={pipeStatus?.next_run_at ?? null}
          schedulerEnabled={pipeStatus?.scheduler_enabled ?? false}
          intervalMinutes={pipeStatus?.scrape_interval_minutes ?? 180}
          profile={profile}
          onEditSetup={() => (profile ? setShowWizard(true) : setView('profile'))}
        >
          {huntButton(false)}
        </Sidebar>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 md:py-8 lg:px-10">
          <div className="mx-auto max-w-5xl">
            {loading ? (
              <div className="flex items-center justify-center py-24 text-low">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Warming up the console…
              </div>
            ) : (
              <motion.div
                key={view}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
              >
                {view === 'dashboard' && (
                  <DashboardView
                    stats={stats}
                    pipeStatus={pipeStatus}
                    pipelineResult={pipelineResult}
                    pipelineBusy={pipelineBusy}
                    matchPolling={matchPolling}
                    profileStatus={status}
                    openDrafts={openDrafts}
                    onOpenMatches={() => setView('matches')}
                    onOpenApplications={() => setView('applications')}
                    onHunt={handleRunPipeline}
                    pendingMatches={matches.filter((m) => !m.decision)}
                    onDecision={handleDecision}
                    onPrepare={handlePrepare}
                    preparedJobIds={preparedJobIds}
                  />
                )}

                {view === 'matches' && (
                  <section>
                    <ViewHeader
                      title="Matches"
                      sub="Every job the AI has ranked against your CV. Approve the ones worth your time."
                    />
                    <div className="mb-5 inline-flex rounded-lg border border-line bg-ink p-1">
                      {(
                        [
                          ['pending', 'Awaiting my decision'],
                          ['approved', 'Approved'],
                          ['all', 'All'],
                        ] as const
                      ).map(([id, label]) => (
                        <button
                          key={id}
                          onClick={() => setMatchesFilter(id)}
                          className={cn(
                            'rounded-md px-3 py-1.5 text-sm transition-colors',
                            matchesFilter === id
                              ? 'bg-surface-2 text-hi'
                              : 'text-low hover:text-mid'
                          )}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    {filteredMatches.length === 0 ? (
                      <Empty
                        icon={<Inbox className="h-8 w-8" />}
                        title="No matches here yet"
                        body="Hunt now to scrape job sites and match them against your CV."
                      />
                    ) : (
                      <div className="space-y-3">
                        {filteredMatches.map((m) => (
                          <MatchCard
                            key={m.id}
                            match={m}
                            onDecision={handleDecision}
                            onPrepare={handlePrepare}
                            prepared={preparedJobIds.has(m.job_id)}
                          />
                        ))}
                      </div>
                    )}
                  </section>
                )}

                {view === 'applications' && (
                  <ApplicationsView
                    drafts={drafts}
                    applications={applications}
                    onChanged={refresh}
                    onPrepare={handlePrepare}
                  />
                )}

                {view === 'profile' && (
                  <ProfileView
                    profile={profile}
                    onUpload={handleUpload}
                    onSaved={refresh}
                    onEditSetup={() => setShowWizard(true)}
                  />
                )}
              </motion.div>
            )}
          </div>
        </main>
      </div>

      {/* Onboarding wizard — auto-shows until setup is done, re-openable anytime */}
      {showWizard && profile && (
        <OnboardingWizard onComplete={handleOnboardingComplete} onClose={() => setShowWizard(false)} />
      )}
    </div>
  );
}

// ---------------- Dashboard ----------------

function DashboardView({
  stats,
  pipeStatus,
  pipelineResult,
  pipelineBusy,
  matchPolling,
  profileStatus,
  openDrafts,
  onOpenMatches,
  onOpenApplications,
  onHunt,
  pendingMatches,
  onDecision,
  onPrepare,
  preparedJobIds,
}: {
  stats: ProfileStatus['stats'] | undefined;
  pipeStatus: PipelineStatusResponse | null;
  pipelineResult: PipelineRunResponse | null;
  pipelineBusy: boolean;
  matchPolling: boolean;
  profileStatus: ProfileStatus | null;
  openDrafts: number;
  onOpenMatches: () => void;
  onOpenApplications: () => void;
  onHunt: () => void;
  pendingMatches: Match[];
  onDecision: (matchId: number, decision: 'approved' | 'rejected') => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  preparedJobIds: Set<number>;
}) {
  const decisions = [...pendingMatches].sort((a, b) => b.score - a.score).slice(0, 5);
  const matchFailed = pipelineResult?.match?.status === 'failed';
  // Group rows into whole hunts: a run's sources finish seconds apart, separate
  // hunts are hours apart. Rows within 10 minutes of the newest row = last hunt.
  const runs = pipeStatus?.recent_runs ?? [];
  const latestAt = runs[0] ? new Date(runs[0].started_at).getTime() : 0;
  const lastHunt = latestAt
    ? runs.filter((r) => latestAt - new Date(r.started_at).getTime() < 10 * 60 * 1000)
    : [];
  const newSinceLastRun = lastHunt.reduce((sum, r) => sum + r.jobs_new, 0);

  return (
    <section className="space-y-6">
      <HuntPulse
        stats={stats}
        openDrafts={openDrafts}
        newSinceLastRun={newSinceLastRun}
        matchingRunning={matchPolling}
        onOpenMatches={onOpenMatches}
        onOpenApplications={onOpenApplications}
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
        {/* Next decisions — the main column */}
        <div>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="font-semibold tracking-tight text-hi">
              Next decisions
              {decisions.length > 0 && (
                <span className="num ml-2 text-sm font-normal text-low">{decisions.length}</span>
              )}
            </h2>
            {pendingMatches.length > 0 && (
              <button
                onClick={onOpenMatches}
                className="text-sm text-signal transition-colors hover:text-signal/80"
              >
                View all matches →
              </button>
            )}
          </div>
          {decisions.length > 0 ? (
            <div className="space-y-3">
              {decisions.map((m) => (
                <MatchCard
                  key={m.id}
                  match={m}
                  onDecision={onDecision}
                  onPrepare={onPrepare}
                  prepared={preparedJobIds.has(m.job_id)}
                />
              ))}
            </div>
          ) : (
            <Empty
              icon={<Radar className="h-8 w-8" />}
              title={
                pipelineBusy
                  ? 'Scraping job sites…'
                  : matchPolling
                    ? 'AI is ranking jobs for you…'
                    : 'Nothing waiting on you'
              }
              body={
                pipelineBusy
                  ? 'Fetching new postings from all sources.'
                  : matchPolling
                    ? 'Each job takes ~20-60s to score — matches appear live below as they finish.'
                    : 'When the next hunt finds matches worth your attention, they land here for a yes or no.'
              }
            />
          )}
        </div>

        {/* Status rail */}
        <aside className="space-y-4">
          {/* Blockers first */}
          {profileStatus && !profileStatus.has_profile && (
            <Warning>
              No CV on file — upload your CV in <b>Profile</b> to enable AI matching.
            </Warning>
          )}
          {profileStatus && !profileStatus.ai_enabled && (
            <Warning>
              <Cpu className="mr-1.5 inline h-4 w-4" />
              GLM_API_KEY not set on the backend — set it in <code>backend/.env</code> to enable AI
              matching.
            </Warning>
          )}
          {matchFailed && pipelineResult?.match?.error && (
            <Warning>Last hunt failed: {pipelineResult.match.error}</Warning>
          )}
          {/* Email-apply config is deliberately NOT banner-warned: the platform's
              email path is Composio connected-email (see CLAUDE.md decided
              architecture), and the draft card already shows a precise error at
              submit time if Resend keys are missing. Warn at the action, not
              permanently. */}

          {/* The schedule */}
          <div className="rounded-xl border border-line bg-surface/80 p-4">
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-low">
              Automatic hunts
            </p>
            <div className="mt-2">
              <NextHunt
                nextRunAt={pipeStatus?.next_run_at ?? null}
                schedulerEnabled={pipeStatus?.scheduler_enabled ?? false}
                intervalMinutes={pipeStatus?.scrape_interval_minutes ?? 180}
              />
            </div>
            <p className="mt-2 text-xs leading-relaxed text-low">
              {pipeStatus?.scheduler_enabled
                ? `The console hunts by itself every ${Math.round((pipeStatus?.scrape_interval_minutes ?? 180) / 60)}h — new jobs are scraped, deduplicated, and scored while you're away.`
                : 'Automatic hunts are off. Start one yourself whenever you like.'}
            </p>
            {matchPolling && (
              <p className="mt-2 text-xs text-signal" role="status">
                Matching now — results stream into “Next decisions”…
              </p>
            )}
          </div>

          {/* Source health — the complete last hunt, every source */}
          {lastHunt.length > 0 && (
            <div className="rounded-xl border border-line bg-surface/80 p-4">
              <p className="mb-2.5 text-[10px] font-medium uppercase tracking-[0.18em] text-low">
                Last hunt · sources
              </p>
              <div className="space-y-1.5">
                {lastHunt.map((r, i) => (
                  <div key={`${r.source}-${i}`} className="flex items-center justify-between gap-3 text-xs">
                    <span className="truncate text-mid">{r.source}</span>
                    <span
                      className={cn(
                        'num shrink-0',
                        r.status === 'completed'
                          ? 'text-ok'
                          : r.status === 'skipped'
                            ? 'text-low'
                            : 'text-bad'
                      )}
                      title={r.error ?? undefined}
                    >
                      {r.status === 'completed'
                        ? `${r.jobs_found}↑ ${r.jobs_new}+`
                        : r.status === 'skipped'
                          ? 'skipped'
                          : 'failed'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Manual trigger for wide screens without sidebar visibility? No —
              sidebar always has it; keep a quiet text link for convenience. */}
          <button
            onClick={onHunt}
            disabled={pipelineBusy || matchPolling}
            className="text-xs text-low transition-colors hover:text-signal disabled:opacity-50"
          >
            Can&apos;t wait? Hunt now →
          </button>
        </aside>
      </div>
    </section>
  );
}

// ---------------- Applications (draft review + sent history) ----------------

function ViewHeader({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mb-5">
      <h1 className="text-lg font-semibold tracking-tight text-hi">{title}</h1>
      <p className="mt-0.5 text-sm text-mid">{sub}</p>
    </div>
  );
}

function ApplicationsView({
  drafts,
  applications,
  onChanged,
}: {
  drafts: ApplicationDraft[];
  applications: (Application & { job?: { title: string; company: string | null } })[];
  onChanged: () => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
}) {
  const openDrafts = drafts.filter((d) => d.status !== 'submitted');

  return (
    <div className="space-y-10">
      {/* Stage 1: drafted applications awaiting review */}
      <section>
        <ViewHeader
          title="Review before sending"
          sub="AI tailored your CV and cover letter to each approved job. Read, edit if you want, then send. Nothing goes out without you."
        />
        {openDrafts.length === 0 ? (
          <Empty
            icon={<FileText className="h-8 w-8" />}
            title="No drafts waiting"
            body="Approve a match and press “Prepare application” — the AI will tailor your CV and cover letter for that job."
          />
        ) : (
          <div className="space-y-3">
            {openDrafts.map((d) => (
              <DraftCard key={d.id} draft={d} onChanged={onChanged} />
            ))}
          </div>
        )}
      </section>

      {/* Stage 2: sent history */}
      <section>
        <ViewHeader title="Sent applications" sub="Everything you've released — by email or through a browser." />
        {applications.length === 0 ? (
          <Empty
            icon={<Send className="h-8 w-8" />}
            title="Nothing sent yet"
            body="Once you approve a draft, it lands here — sent by email or opened in your browser."
          />
        ) : (
          <div className="space-y-3">
            {applications.map((a) => (
              <div
                key={a.id}
                className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface/80 p-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-hi">
                    {a.job?.title ?? `Job #${a.job_id}`}
                  </p>
                  <p className="text-sm text-low">
                    {a.job?.company} · {a.method} · {timeAgo(a.created_at)}
                  </p>
                  {a.error && <p className="mt-1 text-sm text-bad">{a.error}</p>}
                </div>
                <span
                  className={cn(
                    'rounded-full px-2.5 py-1 text-xs font-medium',
                    a.status === 'sent' && 'bg-ok/15 text-ok',
                    a.status === 'manual_pending' && 'bg-signal/15 text-signal',
                    a.status === 'failed' && 'bg-bad/15 text-bad',
                    a.status === 'queued' && 'bg-surface-2 text-mid'
                  )}
                >
                  {a.status === 'sent' ? 'sent ✓' : a.status.replace('_', ' ')}
                </span>
                {a.status === 'manual_pending' && a.apply_url && (
                  <a
                    href={a.apply_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
                  >
                    <ExternalLink className="h-4 w-4" /> Open posting
                  </a>
                )}
                {a.status === 'failed' && a.method === 'email' && (
                  <button
                    onClick={async () => {
                      await retryApplication(a.id);
                      onChanged();
                    }}
                    className="rounded-lg bg-signal px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-signal/90"
                  >
                    Retry
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

// A single draft: read/edit cover letter + tailored CV, then submit
function DraftCard({
  draft,
  onChanged,
}: {
  draft: ApplicationDraft;
  onChanged: () => Promise<void>;
}) {
  const [coverLetter, setCoverLetter] = useState(draft.cover_letter ?? '');
  const [tailoredCv, setTailoredCv] = useState(draft.tailored_cv ?? '');
  const [busy, setBusy] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const job = draft.job;
  const canEmail = Boolean(job?.application_email);

  const save = async () => {
    setBusy('save');
    try {
      await updateDraft(draft.id, { cover_letter: coverLetter, tailored_cv: tailoredCv });
      setDirty(false);
      await onChanged();
    } finally {
      setBusy(null);
    }
  };

  const submit = async (method: 'email' | 'browser') => {
    setSubmitError(null);
    setBusy(`submit-${method}`);
    try {
      if (dirty) {
        await updateDraft(draft.id, { cover_letter: coverLetter, tailored_cv: tailoredCv });
      }
      const application = await submitDraft(draft.id, method);
      if (method === 'browser' && application.apply_url) {
        window.open(application.apply_url, '_blank', 'noopener');
      }
      if (application.status === 'failed' && application.error) {
        setSubmitError(application.error);
      }
      await onChanged();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Submission failed');
    } finally {
      setBusy(null);
    }
  };

  const copyCoverLetter = async () => {
    await navigator.clipboard.writeText(coverLetter);
  };

  // Downloads always reflect saved content — flush pending edits first
  const download = async (kind: 'cover-letter' | 'cv') => {
    if (dirty) {
      await updateDraft(draft.id, { cover_letter: coverLetter, tailored_cv: tailoredCv });
      setDirty(false);
    }
    const url =
      kind === 'cover-letter' ? draftCoverLetterPdfUrl(draft.id) : draftCvPdfUrl(draft.id);
    window.open(url, '_blank', 'noopener');
  };

  return (
    <div className="rounded-xl border border-line bg-surface/80 p-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate font-semibold text-hi">
          {job?.title ?? `Job #${draft.job_id}`}
          <span className="ml-2 text-sm font-normal text-low">{job?.company}</span>
        </h3>
        {draft.status === 'drafting' && (
          <span className="inline-flex items-center rounded-full bg-surface-2 px-2.5 py-1 text-xs text-mid">
            <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> tailoring…
          </span>
        )}
        {draft.status === 'ready' && (
          <span className="rounded-full bg-signal/15 px-2.5 py-1 text-xs text-signal">
            ready for your review
          </span>
        )}
        {draft.status === 'failed' && (
          <span className="rounded-full bg-bad/15 px-2.5 py-1 text-xs text-bad">failed</span>
        )}
      </div>

      {draft.status === 'failed' && draft.error && (
        <p className="mt-2 text-sm text-bad">{draft.error}</p>
      )}

      {/* What the AI changed */}
      {draft.changes_summary.length > 0 && (
        <div className="mt-3 rounded-lg border border-line bg-ink/60 p-3">
          <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-low">
            What the AI changed for this application
          </p>
          <ul className="space-y-1">
            {draft.changes_summary.map((c, i) => (
              <li key={i} className="text-sm text-mid">
                • {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      {draft.status === 'ready' && (
        <>
          {/* Cover letter editor */}
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                Cover letter (sent to the employer)
              </p>
              <div className="flex items-center gap-3">
                <button
                  onClick={copyCoverLetter}
                  className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                >
                  <Copy className="h-3.5 w-3.5" /> Copy
                </button>
                <button
                  onClick={() => download('cover-letter')}
                  className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                >
                  <Download className="h-3.5 w-3.5" /> PDF
                </button>
              </div>
            </div>
            <textarea
              value={coverLetter}
              onChange={(e) => {
                setCoverLetter(e.target.value);
                setDirty(true);
              }}
              rows={10}
              className="w-full rounded-lg border border-line bg-ink p-3 font-mono text-sm leading-relaxed text-hi outline-none transition-colors focus:border-signal"
            />
          </div>

          {/* Tailored CV editor */}
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                Your CV, tailored for this job
              </p>
              <button
                onClick={() => download('cv')}
                className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
              >
                <Download className="h-3.5 w-3.5" /> PDF
              </button>
            </div>
            <textarea
              value={tailoredCv}
              onChange={(e) => {
                setTailoredCv(e.target.value);
                setDirty(true);
              }}
              rows={16}
              className="w-full rounded-lg border border-line bg-ink p-3 font-mono text-sm leading-relaxed text-hi outline-none transition-colors focus:border-signal"
            />
          </div>

          {submitError && (
            <p className="mt-3 rounded-lg bg-bad/10 p-3 text-sm text-bad" role="alert">
              {submitError}
            </p>
          )}

          {/* Actions */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              onClick={save}
              disabled={!dirty || busy !== null}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3.5 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi disabled:opacity-40"
            >
              <Save className="h-4 w-4" /> {busy === 'save' ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
            </button>

            <span className="mx-1 h-6 w-px bg-line" />

            {canEmail ? (
              <button
                onClick={() => submit('email')}
                disabled={busy !== null}
                className="inline-flex items-center gap-1.5 rounded-lg bg-ok px-4 py-2 text-sm font-medium text-ink transition hover:bg-ok/90 active:scale-[0.98] disabled:opacity-50"
              >
                <Mail className="h-4 w-4" />
                {busy === 'submit-email' ? 'Sending…' : 'Approve & send by email'}
              </button>
            ) : (
              <span className="text-xs text-low">no application email published</span>
            )}
            <button
              onClick={() => submit('browser')}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              <MousePointerClick className="h-4 w-4" />
              {busy === 'submit-browser' ? 'Opening…' : 'Approve & apply in browser'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------- Profile ----------------

function ProfileView({
  profile,
  onUpload,
  onSaved,
  onEditSetup,
}: {
  profile: Profile | null;
  onUpload: (file: File) => Promise<void>;
  onSaved: () => Promise<void>;
  onEditSetup: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [preferredRoles, setPreferredRoles] = useState('');
  const [excludeKeywords, setExcludeKeywords] = useState('');

  useEffect(() => {
    setPreferredRoles(profile?.preferred_roles?.join(', ') ?? '');
    setExcludeKeywords(profile?.exclude_keywords?.join(', ') ?? '');
  }, [profile]);

  const save = async () => {
    setSaving(true);
    try {
      await updateProfile({
        preferred_roles: preferredRoles.split(',').map((s) => s.trim()).filter(Boolean),
        exclude_keywords: excludeKeywords.split(',').map((s) => s.trim()).filter(Boolean),
        ...(profile?.full_name ? {} : {}),
      });
      await onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-6">
      <ViewHeader
        title="Profile"
        sub="Your CV is the source of truth — the original file is never modified. Tailored copies live with each application."
      />
      <CvUpload onUploaded={onUpload} hasExistingCv={!!profile?.cv_file_name} />

      {profile && (
        <>
          {/* Search setup — the onboarding result, always visible/editable */}
          <div className="rounded-xl border border-line bg-surface/80 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-semibold text-hi">Your search setup</h3>
              <button
                onClick={onEditSetup}
                className="rounded-lg border border-line px-3 py-1.5 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
              >
                Edit setup
              </button>
            </div>
            {profile.onboarded ? (
              <div className="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-low">Where</p>
                  <p className="mt-1 text-hi">
                    {profile.country === 'SE' ? 'Sweden' : profile.country === 'GB' ? 'United Kingdom' : profile.country}
                    {profile.municipality ? ` · ${profile.municipality}` : profile.region ? ` · ${profile.region}` : ''}
                    {profile.remote_only ? ' · remote only' : ''}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-low">
                    Hunting for ({profile.search_queries.length} titles)
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {profile.search_queries.map((q) => (
                      <span key={q} className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs text-mid">
                        {q}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-low">
                Not set up yet — the wizard sets your country, area, and job titles from your CV.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-line bg-surface/80 p-5">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold text-hi">{profile.full_name ?? 'Your profile'}</h2>
              {profile.professional_title && (
                <span className="rounded-full border border-line bg-surface-2 px-2.5 py-0.5 text-sm text-mid">
                  {profile.professional_title}
                </span>
              )}
              {profile.experience_years != null && (
                <span className="num text-sm text-low">{profile.experience_years} yrs exp</span>
              )}
            </div>
            {profile.ai_summary && <p className="mt-2 text-sm text-mid">{profile.ai_summary}</p>}
            {profile.skills.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {profile.skills.slice(0, 20).map((s, i) => (
                  <span
                    key={`${s.name}-${i}`}
                    className="rounded-md border border-line bg-surface-2 px-2 py-0.5 text-xs text-mid"
                  >
                    {s.name}
                    {s.level ? ` · ${s.level}` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-xl border border-line bg-surface/80 p-5">
            <h3 className="mb-3 font-semibold text-hi">Job search preferences</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                  Preferred roles (comma separated)
                </span>
                <input
                  value={preferredRoles}
                  onChange={(e) => setPreferredRoles(e.target.value)}
                  placeholder="Backend Developer, Python Developer"
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                  Exclude keywords (jobs skipped)
                </span>
                <input
                  value={excludeKeywords}
                  onChange={(e) => setExcludeKeywords(e.target.value)}
                  placeholder="senior, scala, on-site"
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
            </div>
            <button
              onClick={save}
              disabled={saving}
              className="mt-4 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save preferences'}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

// ---------------- Shared ----------------

function Warning({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad/10 p-3 text-sm text-hi" role="alert">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-bad" />
      <div>{children}</div>
    </div>
  );
}

function Empty({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line-2 py-16 text-center">
      <div className="mb-3 text-low">{icon}</div>
      <p className="font-medium text-mid">{title}</p>
      <p className="mt-1 max-w-sm text-sm text-low">{body}</p>
    </div>
  );
}
