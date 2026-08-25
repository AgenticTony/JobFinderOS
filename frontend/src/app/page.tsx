'use client';

// JobFinderOS dashboard — the job hunter's operating system.
// Pipeline: scrape -> match vs CV -> approve -> tailor CV & cover letter -> review -> send.

import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  AlertTriangle,
  Briefcase,
  ClipboardList,
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
  User,
} from 'lucide-react';
import CvUpload from '@/components/CvUpload';
import MatchCard from '@/components/MatchCard';
import OnboardingWizard from '@/components/OnboardingWizard';
import StatCard from '@/components/StatCard';
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
  Profile,
  ProfileStatus,
} from '@/types';
import { cn, timeAgo } from '@/lib/utils';

type Tab = 'dashboard' | 'matches' | 'applications' | 'profile';

export default function Home() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [status, setStatus] = useState<ProfileStatus | null>(null);
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
    const [statusRes, profileRes] = await Promise.allSettled([getProfileStatus(), getProfile()]);
    if (statusRes.status === 'fulfilled') setStatus(statusRes.value);
    if (profileRes.status === 'fulfilled') setProfile(profileRes.value);
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

  // While background matching runs, poll status + stream fresh matches in
  useEffect(() => {
    if (!matchPolling) return;
    const poll = setInterval(async () => {
      try {
        const [st, ms] = await Promise.all([
          getPipelineStatus(),
          getMatches({ limit: 200 }),
        ]);
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
    setTab('applications'); // take the user straight to the review stage
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

  const stats = status?.stats;

  return (
    <div className="os-backdrop min-h-screen text-zinc-100">
      <div className="mx-auto max-w-6xl px-4 pb-16 pt-8">
        {/* Header */}
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-sky-500 to-violet-600">
              <Radar className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">JobFinderOS</h1>
              <p className="text-xs text-zinc-500">
                scrape → match → approve → tailor → review → send · powered by the TalentHive engine
              </p>
            </div>
          </div>
          <button
            onClick={handleRunPipeline}
            disabled={pipelineBusy || matchPolling}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-600/20 transition hover:bg-sky-500 disabled:opacity-60"
          >
            {pipelineBusy || matchPolling ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {pipelineBusy
              ? 'Scraping job sites…'
              : matchPolling
                ? 'AI matching in background…'
                : 'Run Pipeline'}
          </button>
        </header>

        {/* Readiness warnings */}
        {status && !loading && (
          <div className="mb-6 space-y-2">
            {!status.has_profile && (
              <Warning>
                No CV on file — upload your CV in the <b>Profile</b> tab to enable AI matching.
              </Warning>
            )}
            {!status.ai_enabled && (
              <Warning>
                <Cpu className="mr-1.5 inline h-4 w-4" />
                GLM_API_KEY not set on the backend — set it in <code>backend/.env</code> (your
                TalentHive key works) to enable AI matching.
              </Warning>
            )}
            {status.ai_enabled && !status.email_apply_enabled && (
              <Warning tone="amber">
                Email auto-apply disabled — set RESEND_API_KEY + APPLY_FROM_EMAIL in{' '}
                <code>backend/.env</code> to enable one-click email applications.
              </Warning>
            )}
          </div>
        )}

        {/* Tabs */}
        <nav className="mb-6 flex gap-1 rounded-xl border border-white/10 bg-white/[0.03] p-1">
          {(
            [
              ['dashboard', 'Dashboard', <Briefcase key="i" className="h-4 w-4" />],
              ['matches', 'Matches', <ClipboardList key="i" className="h-4 w-4" />],
              ['applications', 'Applications', <Send key="i" className="h-4 w-4" />],
              ['profile', 'Profile', <User key="i" className="h-4 w-4" />],
            ] as [Tab, string, React.ReactNode][]
          ).map(([id, label, icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                'flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition',
                tab === id ? 'bg-white/10 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'
              )}
            >
              {icon}
              {label}
              {id === 'matches' && stats?.matches_pending_decision ? (
                <span className="rounded-full bg-sky-500/20 px-1.5 text-xs text-sky-300">
                  {stats.matches_pending_decision}
                </span>
              ) : null}
            </button>
          ))}
        </nav>

        {loading ? (
          <div className="flex items-center justify-center py-24 text-zinc-500">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading your OS…
          </div>
        ) : (
          <motion.main key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
            {tab === 'dashboard' && (
              <DashboardView
                stats={stats}
                pipelineResult={pipelineResult}
                pipelineBusy={pipelineBusy}
                matchPolling={matchPolling}
                onOpenMatches={() => setTab('matches')}
                topMatches={pipelineResult?.top_matches ?? []}
                onDecision={handleDecision}
                onPrepare={handlePrepare}
                preparedJobIds={preparedJobIds}
              />
            )}

            {tab === 'matches' && (
              <section>
                <div className="mb-4 flex gap-2">
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
                        'rounded-lg px-3 py-1.5 text-sm transition',
                        matchesFilter === id
                          ? 'bg-white/10 text-zinc-100'
                          : 'text-zinc-500 hover:text-zinc-300'
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
                    body="Run the pipeline to scrape job sites and match them against your CV."
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

            {tab === 'applications' && (
              <ApplicationsView
                drafts={drafts}
                applications={applications}
                onChanged={refresh}
                onPrepare={handlePrepare}
              />
            )}

            {tab === 'profile' && (
              <ProfileView
                profile={profile}
                onUpload={handleUpload}
                onSaved={refresh}
                onEditSetup={() => setShowWizard(true)}
              />
            )}
          </motion.main>
        )}
      </div>

      {/* Onboarding wizard — auto-shows until setup is done, re-openable anytime */}
      {showWizard && profile && (
        <OnboardingWizard onComplete={handleOnboardingComplete} onClose={() => setShowWizard(false)} />
      )}
    </div>
  );
}

// ---------------- Applications (draft review + sent history) ----------------

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
    <div className="space-y-8">
      {/* Stage 1: drafted applications awaiting review */}
      <section>
        <h2 className="mb-1 font-semibold text-zinc-200">Review before sending</h2>
        <p className="mb-4 text-sm text-zinc-500">
          AI tailored your CV and cover letter to each approved job. Read, edit if you want, then
          send. Nothing goes out without you.
        </p>
        {openDrafts.length === 0 ? (
          <Empty
            icon={<FileText className="h-8 w-8" />}
            title="No drafts waiting"
            body="Approve a match in the Matches tab and press 'Prepare application' — the AI will tailor your CV and cover letter for that job."
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
        <h2 className="mb-4 font-semibold text-zinc-200">Sent applications</h2>
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
                className="flex flex-wrap items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-zinc-100">
                    {a.job?.title ?? `Job #${a.job_id}`}
                  </p>
                  <p className="text-sm text-zinc-500">
                    {a.job?.company} · {a.method} · {timeAgo(a.created_at)}
                  </p>
                  {a.error && <p className="mt-1 text-sm text-rose-400">{a.error}</p>}
                </div>
                <span
                  className={cn(
                    'rounded-full px-2.5 py-1 text-xs font-medium',
                    a.status === 'sent' && 'bg-emerald-500/15 text-emerald-400',
                    a.status === 'manual_pending' && 'bg-amber-500/15 text-amber-400',
                    a.status === 'failed' && 'bg-rose-500/15 text-rose-400',
                    a.status === 'queued' && 'bg-zinc-500/15 text-zinc-400'
                  )}
                >
                  {a.status === 'sent' ? 'sent ✓' : a.status.replace('_', ' ')}
                </span>
                {a.status === 'manual_pending' && a.apply_url && (
                  <a
                    href={a.apply_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5 text-sm text-zinc-300 hover:bg-white/5"
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
                    className="rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500"
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
    <div className="rounded-xl border border-sky-500/20 bg-white/[0.03] p-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate font-semibold text-zinc-100">
          {job?.title ?? `Job #${draft.job_id}`}
          <span className="ml-2 text-sm font-normal text-zinc-500">{job?.company}</span>
        </h3>
        {draft.status === 'drafting' && (
          <span className="rounded-full bg-zinc-500/15 px-2.5 py-1 text-xs text-zinc-400">
            <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> tailoring…
          </span>
        )}
        {draft.status === 'ready' && (
          <span className="rounded-full bg-sky-500/15 px-2.5 py-1 text-xs text-sky-400">
            ready for your review
          </span>
        )}
        {draft.status === 'failed' && (
          <span className="rounded-full bg-rose-500/15 px-2.5 py-1 text-xs text-rose-400">failed</span>
        )}
      </div>

      {draft.status === 'failed' && draft.error && (
        <p className="mt-2 text-sm text-rose-400">{draft.error}</p>
      )}

      {/* What the AI changed */}
      {draft.changes_summary.length > 0 && (
        <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
            What the AI changed for this application
          </p>
          <ul className="space-y-1">
            {draft.changes_summary.map((c, i) => (
              <li key={i} className="text-sm text-zinc-400">
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
              <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                Cover letter (sent to the employer)
              </p>
              <div className="flex items-center gap-3">
                <button
                  onClick={copyCoverLetter}
                  className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
                >
                  <Copy className="h-3.5 w-3.5" /> Copy
                </button>
                <button
                  onClick={() => download('cover-letter')}
                  className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
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
              className="w-full rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-sm leading-relaxed outline-none focus:border-sky-500"
            />
          </div>

          {/* Tailored CV editor */}
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                Your CV, tailored for this job
              </p>
              <button
                onClick={() => download('cv')}
                className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
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
              className="w-full rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-sm leading-relaxed outline-none focus:border-sky-500"
            />
          </div>

          {submitError && (
            <p className="mt-3 rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{submitError}</p>
          )}

          {/* Actions */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              onClick={save}
              disabled={!dirty || busy !== null}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3.5 py-2 text-sm text-zinc-300 transition hover:bg-white/5 disabled:opacity-40"
            >
              <Save className="h-4 w-4" /> {busy === 'save' ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
            </button>

            <span className="mx-1 h-6 w-px bg-white/10" />

            {canEmail ? (
              <button
                onClick={() => submit('email')}
                disabled={busy !== null}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
              >
                <Mail className="h-4 w-4" />
                {busy === 'submit-email' ? 'Sending…' : 'Approve & send by email'}
              </button>
            ) : (
              <span className="text-xs text-zinc-600">no application email published</span>
            )}
            <button
              onClick={() => submit('browser')}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
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

// ---------------- Dashboard ----------------

function DashboardView({
  stats,
  pipelineResult,
  pipelineBusy,
  matchPolling,
  onOpenMatches,
  topMatches,
  onDecision,
  onPrepare,
  preparedJobIds,
}: {
  stats: ProfileStatus['stats'] | undefined;
  pipelineResult: PipelineRunResponse | null;
  pipelineBusy: boolean;
  matchPolling: boolean;
  onOpenMatches: () => void;
  topMatches: Match[];
  onDecision: (matchId: number, decision: 'approved' | 'rejected') => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  preparedJobIds: Set<number>;
}) {
  return (
    <section className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Jobs scraped" value={stats?.jobs_total ?? 0} />
        <StatCard
          label="Awaiting decision"
          value={stats?.matches_pending_decision ?? 0}
          accent="text-sky-400"
          hint="recommended, not yet approved"
        />
        <StatCard label="Approved" value={stats?.jobs_approved ?? 0} accent="text-emerald-400" />
        <StatCard label="Applied" value={stats?.jobs_applied ?? 0} accent="text-violet-400" />
      </div>

      {pipelineResult && (
        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
          <p className="mb-3 text-sm font-semibold text-zinc-200">Last pipeline run</p>
          <div className="space-y-1.5">
            {pipelineResult.scrape.map((s) => (
              <div key={s.source} className="flex items-center justify-between text-sm">
                <span className="text-zinc-400">{s.source}</span>
                <span className={s.status === 'completed' ? 'text-emerald-400' : 'text-rose-400'}>
                  {s.status === 'completed'
                    ? `${s.jobs_found} found · ${s.jobs_new} new`
                    : s.error ?? 'failed'}
                </span>
              </div>
            ))}
            {pipelineResult.match && (
              <div className="flex items-center justify-between border-t border-white/5 pt-2 text-sm">
                <span className="text-zinc-400">AI matching</span>
                <span
                  className={
                    pipelineResult.match.status === 'completed' ? 'text-emerald-400' : 'text-amber-400'
                  }
                >
                  {pipelineResult.match.error ??
                    `${pipelineResult.match.matches_created} matched of ${pipelineResult.match.jobs_considered}`}
                </span>
              </div>
            )}
            {matchPolling && (
              <div className="flex items-center justify-between border-t border-white/5 pt-2 text-sm">
                <span className="text-zinc-400">AI matching</span>
                <span className="inline-flex items-center gap-1.5 text-sky-400">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  running in background — matches appear live
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-zinc-200">Top recommendations</h2>
          <button onClick={onOpenMatches} className="text-sm text-sky-400 hover:text-sky-300">
            View all matches →
          </button>
        </div>
        {topMatches.length > 0 ? (
          <div className="space-y-3">
            {topMatches.slice(0, 5).map((m) => (
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
                  : 'No recommendations yet'
            }
            body={
              pipelineBusy
                ? 'Fetching new postings from all sources.'
                : matchPolling
                  ? 'Each job takes ~20-60s to score — matches stream live into the Matches tab.'
                  : 'Press "Run Pipeline" to scrape job sites and let the AI rank them for you.'
            }
          />
        )}
      </div>
    </section>
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
      <CvUpload onUploaded={onUpload} hasExistingCv={!!profile?.cv_file_name} />

      {profile && (
        <>
          {/* Search setup — the onboarding result, always visible/editable */}
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-semibold text-zinc-200">Your search setup</h3>
              <button
                onClick={onEditSetup}
                className="rounded-lg border border-white/15 px-3 py-1.5 text-sm text-zinc-300 hover:bg-white/5"
              >
                Edit setup
              </button>
            </div>
            {profile.onboarded ? (
              <div className="grid gap-2 text-sm sm:grid-cols-2">
                <div>
                  <p className="text-xs uppercase tracking-wide text-zinc-500">Where</p>
                  <p className="text-zinc-200">
                    {profile.country === 'SE' ? '🇸🇪 Sweden' : profile.country === 'GB' ? '🇬🇧 United Kingdom' : profile.country}
                    {profile.municipality ? ` · ${profile.municipality}` : profile.region ? ` · ${profile.region}` : ''}
                    {profile.remote_only ? ' · remote only' : ''}
                  </p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-zinc-500">
                    Hunting for ({profile.search_queries.length} titles)
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {profile.search_queries.map((q) => (
                      <span key={q} className="rounded-full bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300">
                        {q}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-zinc-500">
                Not set up yet — the wizard sets your country, area, and job titles from your CV.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">{profile.full_name ?? 'Your profile'}</h2>
              {profile.professional_title && (
                <span className="rounded-full bg-white/5 px-2.5 py-0.5 text-sm text-zinc-400">
                  {profile.professional_title}
                </span>
              )}
              {profile.experience_years != null && (
                <span className="text-sm text-zinc-500">{profile.experience_years} yrs exp</span>
              )}
            </div>
            {profile.ai_summary && <p className="mt-2 text-sm text-zinc-400">{profile.ai_summary}</p>}
            {profile.skills.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {profile.skills.slice(0, 20).map((s, i) => (
                  <span
                    key={`${s.name}-${i}`}
                    className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-zinc-300"
                  >
                    {s.name}
                    {s.level ? ` · ${s.level}` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
            <h3 className="mb-3 font-semibold text-zinc-200">Job search preferences</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">
                  Preferred roles (comma separated)
                </span>
                <input
                  value={preferredRoles}
                  onChange={(e) => setPreferredRoles(e.target.value)}
                  placeholder="Backend Developer, Python Developer"
                  className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-sky-500"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">
                  Exclude keywords (jobs skipped)
                </span>
                <input
                  value={excludeKeywords}
                  onChange={(e) => setExcludeKeywords(e.target.value)}
                  placeholder="senior, scala, on-site"
                  className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-sky-500"
                />
              </label>
            </div>
            <button
              onClick={save}
              disabled={saving}
              className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
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

function Warning({ children, tone = 'rose' }: { children: React.ReactNode; tone?: 'rose' | 'amber' }) {
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-lg border p-3 text-sm',
        tone === 'rose'
          ? 'border-rose-500/30 bg-rose-500/10 text-rose-200'
          : 'border-amber-500/30 bg-amber-500/10 text-amber-200'
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
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
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-white/10 py-16 text-center">
      <div className="mb-3 text-zinc-600">{icon}</div>
      <p className="font-medium text-zinc-300">{title}</p>
      <p className="mt-1 max-w-sm text-sm text-zinc-500">{body}</p>
    </div>
  );
}
