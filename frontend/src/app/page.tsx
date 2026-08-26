'use client';

// JobFinderOS — the hunting console.
// Pipeline: scrape -> match vs CV -> approve -> tailor CV & cover letter -> review -> send.
// Shell: left sidebar (nav + live hunt status) on md+, compact top bar on mobile.
// Landing view is the Dashboard: Hunt Pulse funnel + next decisions + status rail.

import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertTriangle,
  ChevronDown,
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
import { cn, parseUtcDate, timeAgo } from '@/lib/utils';
import { apiErrorMessage, connectComposio, getIntegrations } from '@/lib/api';
import type { IntegrationsStatus } from '@/types';

export default function Home() {
  const [view, setView] = useState<View>('dashboard');
  const [status, setStatus] = useState<ProfileStatus | null>(null);
  const [pipeStatus, setPipeStatus] = useState<PipelineStatusResponse | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [matches, setMatches] = useState<Match[]>([]);
  const [drafts, setDrafts] = useState<ApplicationDraft[]>([]);
  const [applications, setApplications] = useState<(Application & { job?: { title: string; company: string | null } })[]>([]);
  const [pipelineBusy, setPipelineBusy] = useState(false);
  const [matchPolling, setMatchPolling] = useState(false);
  const [pipelineResult, setPipelineResult] = useState<PipelineRunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);
  // Sidebar rail/expanded choice — persisted so the console opens the way
  // the user left it. Adopted after mount; written only on user toggle
  // (never on mount, so a stray initial render can't clobber the choice).
  const [railCollapsed, setRailCollapsed] = useState(false);
  useEffect(() => {
    setRailCollapsed(localStorage.getItem('jfos-rail-collapsed') === '1');
  }, []);
  const toggleRail = () =>
    setRailCollapsed((c) => {
      const next = !c;
      localStorage.setItem('jfos-rail-collapsed', next ? '1' : '0');
      return next;
    });

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

  // Keep the countdown and source health fresh without hammering the API.
  // When the stats signature changes (a scheduled hunt ran in the background),
  // pull the full lists too — otherwise the cards go stale while the counts
  // update, which reads as "new jobs but nothing new".
  const lastStatsSig = useRef<string | null>(null);
  useEffect(() => {
    const id = setInterval(() => {
      getPipelineStatus()
        .then((st) => {
          setPipeStatus(st);
          const sig = JSON.stringify(st.stats);
          if (lastStatsSig.current !== null && sig !== lastStatsSig.current) {
            refresh();
          }
          lastStatsSig.current = sig;
        })
        .catch(() => {});
    }, 60_000);
    return () => clearInterval(id);
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
    setView('apps-review'); // take the user straight to the review stage
    await refresh();
  };

  const preparedJobIds = new Set(
    drafts.filter((d) => d.status === 'ready' || d.status === 'submitted').map((d) => d.job_id)
  );

  // Approved but not yet applied — the hunt isn't done until it's sent.
  // These stay on the dashboard so a distracted "I'm sure I applied" never happens.
  const appliedJobIds = new Set(applications.map((a) => a.job_id));
  const finishApplying = matches
    .filter((m) => m.decision === 'approved' && !appliedJobIds.has(m.job_id))
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);

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
        <span className={railCollapsed ? 'hidden' : 'hidden lg:inline'}>
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
          {NAV.map(({ id, label, icon: Icon }) => {
            const active =
              view === id ||
              Boolean(NAV.find((n) => n.id === id)?.children?.some((c) => c.id === view));
            return (
            <button
              key={id}
              onClick={() => setView(id)}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium',
                active ? 'text-signal' : 'text-low hover:text-mid'
              )}
            >
              {active && <span className="absolute inset-x-4 top-0 h-0.5 rounded-full bg-signal" />}
              <Icon className="h-[18px] w-[18px]" aria-hidden />
              {label}
              {id === 'matches' && pendingCount > 0 && (
                <span className="num absolute right-3 top-1.5 rounded-full bg-signal/15 px-1.5 text-[10px] text-signal">
                  {pendingCount}
                </span>
              )}
            </button>
            );
          })}
        </nav>
      </header>

      <div className="md:flex">
        <Sidebar
          view={view}
          onNavigate={setView}
          pendingCount={pendingCount}
          reviewCount={openDrafts}
          nextRunAt={pipeStatus?.next_run_at ?? null}
          schedulerEnabled={pipeStatus?.scheduler_enabled ?? false}
          intervalMinutes={pipeStatus?.scrape_interval_minutes ?? 180}
          profile={profile}
          onEditSetup={() => (profile ? setShowWizard(true) : setView('profile'))}
          collapsed={railCollapsed}
          onToggleCollapsed={toggleRail}
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
                    onOpenReview={() => setView('apps-review')}
                    onOpenSent={() => setView('apps-sent')}
                    pendingMatches={matches.filter((m) => !m.decision)}
                    finishApplying={finishApplying}
                    onDecision={handleDecision}
                    onPrepare={handlePrepare}
                    preparedJobIds={preparedJobIds}
                  />
                )}

                {(view === 'matches' || view === 'matches-approved') && (
                  <MatchesView
                    approved={view === 'matches-approved'}
                    matches={matches}
                    preparedJobIds={preparedJobIds}
                    appliedJobIds={appliedJobIds}
                    onDecision={handleDecision}
                    onPrepare={handlePrepare}
                    onSwitch={(v) => setView(v)}
                    subTabsAlways={railCollapsed}
                  />
                )}

                {(view === 'apps-review' || view === 'apps-sent') && (
                  <ApplicationsView
                    page={view}
                    drafts={drafts}
                    applications={applications}
                    onChanged={refresh}
                    onPrepare={handlePrepare}
                    onSwitch={(v) => setView(v)}
                    subTabsAlways={railCollapsed}
                  />
                )}

                {view === 'settings' && <SettingsView />}

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
        <OnboardingWizard
          onComplete={handleOnboardingComplete}
          onClose={() => setShowWizard(false)}
          initialLanguages={profile.languages}
          initialIncludeRemote={profile.include_remote || profile.remote_only}
          initialCountry={profile.country ?? ''}
          initialRegion={profile.region ?? ''}
          initialMunicipality={profile.municipality ?? ''}
          initialQueries={profile.search_queries}
        />
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
  onOpenReview,
  onOpenSent,
  pendingMatches,
  finishApplying,
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
  onOpenReview: () => void;
  onOpenSent: () => void;
  pendingMatches: Match[];
  finishApplying: Match[];
  onDecision: (matchId: number, decision: 'approved' | 'rejected') => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  preparedJobIds: Set<number>;
}) {
  const DAY_MS = 24 * 60 * 60 * 1000;
  // Fresh arrivals first, then the rest of the decision queue (deduped)
  const newMatches = [...pendingMatches]
    .filter((m) => Date.now() - parseUtcDate(m.created_at).getTime() < DAY_MS)
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);
  const newIds = new Set(newMatches.map((m) => m.id));
  const decisions = [...pendingMatches]
    .filter((m) => !newIds.has(m.id))
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);
  const matchFailed = pipelineResult?.match?.status === 'failed';
  // Last hunt = newest scrape-run row; "overdue" when it's been more than
  // ~1.5x the interval (backend asleep or dead — the user should notice)
  const lastHuntAt = pipeStatus?.recent_runs?.[0]?.started_at ?? null;
  const hasRailWarnings =
    Boolean(profileStatus && (!profileStatus.has_profile || !profileStatus.ai_enabled)) ||
    matchFailed ||
    matchPolling;

  return (
    <section className="space-y-6">
      <HuntPulse
        stats={stats}
        openDrafts={openDrafts}
        matchingRunning={matchPolling}
        nextRunAt={pipeStatus?.next_run_at ?? null}
        schedulerEnabled={pipeStatus?.scheduler_enabled ?? false}
        intervalMinutes={pipeStatus?.scrape_interval_minutes ?? 180}
        lastHuntAt={lastHuntAt}
        onOpenMatches={onOpenMatches}
        onOpenReview={onOpenReview}
        onOpenSent={onOpenSent}
      />

      <div className={cn('grid gap-6', hasRailWarnings && 'xl:grid-cols-[minmax(0,1fr)_300px]')}>
        {/* Main column — finish what you started, fresh arrivals, then the queue */}
        <div className="space-y-8">
          {finishApplying.length > 0 && (
            <div>
              <div className="mb-3 flex items-baseline justify-between">
                <h2 className="font-semibold tracking-tight text-hi">
                  Finish applying
                  <span className="num ml-2 text-sm font-normal text-signal">{finishApplying.length}</span>
                  <span className="ml-2 text-sm font-normal text-low">approved, not sent yet</span>
                </h2>
              </div>
              <div className="space-y-3">
                {finishApplying.map((m) => (
                  <MatchCard
                    key={m.id}
                    match={m}
                    onDecision={onDecision}
                    onPrepare={onPrepare}
                    onReview={onOpenReview}
                    prepared={preparedJobIds.has(m.job_id)}
                  />
                ))}
              </div>
            </div>
          )}

          {newMatches.length > 0 && (
            <div>
              <div className="mb-3 flex items-baseline justify-between">
                <h2 className="font-semibold tracking-tight text-hi">
                  New in the last 24h
                  <span className="num ml-2 text-sm font-normal text-low">{newMatches.length}</span>
                </h2>
              </div>
              <div className="space-y-3">
                {newMatches.map((m) => (
                  <MatchCard
                    key={m.id}
                    match={m}
                    onDecision={onDecision}
                    onPrepare={onPrepare}
                    prepared={preparedJobIds.has(m.job_id)}
                  />
                ))}
              </div>
            </div>
          )}

          {(decisions.length > 0 || newMatches.length === 0) && (
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
                        : 'Nothing else waiting'
                  }
                  body={
                    pipelineBusy
                      ? 'Fetching new postings from all sources.'
                      : matchPolling
                        ? 'Each job takes ~20-60s to score — matches appear live below as they finish.'
                        : 'New arrivals appear above; anything still awaiting your decision stays here.'
                  }
                />
              )}
            </div>
          )}
        </div>

        {/* Status rail — only when there's something to say; the schedule
            lives in the Hunt Pulse header now */}
        {hasRailWarnings && (
          <aside className="space-y-4">
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
            {matchPolling && (
              <p className="text-xs text-signal" role="status">
                Matching now — results stream into “Next decisions”…
              </p>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}

// ---------------- Matches ----------------

// Sub-page switcher — only below lg, where the sidebar can't show children
function SubTabs({
  options,
  onSwitch,
  forceVisible = false,
}: {
  options: { id: View; label: string; active: boolean }[];
  onSwitch: (v: View) => void;
  forceVisible?: boolean; // when the sidebar is folded to an icon rail
}) {
  return (
    <div
      className={cn(
        'mb-5 inline-flex rounded-lg border border-line bg-ink p-1',
        !forceVisible && 'lg:hidden'
      )}
    >
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onSwitch(o.id)}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm transition-colors',
            o.active ? 'bg-surface-2 text-hi' : 'text-low hover:text-mid'
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function MatchesView({
  approved,
  matches,
  preparedJobIds,
  appliedJobIds,
  onDecision,
  onPrepare,
  onSwitch,
  subTabsAlways = false,
}: {
  approved: boolean;
  matches: Match[];
  preparedJobIds: Set<number>;
  appliedJobIds: Set<number>;
  onDecision: (matchId: number, decision: 'approved' | 'rejected') => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  onSwitch: (v: View) => void;
  subTabsAlways?: boolean;
}) {
  // Approved page = in flight: approved and not yet sent. Once sent, the
  // job's record lives in Applications → Sent — it leaves Matches for good.
  const list = matches.filter((m) =>
    approved ? m.decision === 'approved' && !appliedJobIds.has(m.job_id) : !m.decision
  );

  return (
    <section>
      {approved ? (
        <ViewHeader
          title="Approved"
          sub="Approved and in flight — jobs stay here until they're sent, then move to Applications → Sent."
        />
      ) : (
        <ViewHeader
          title="Awaiting you"
          sub="Every job the AI has ranked against your CV. Approve the ones worth your time."
        />
      )}
      <SubTabs
        onSwitch={onSwitch}
        forceVisible={subTabsAlways}
        options={[
          { id: 'matches', label: 'Awaiting my decision', active: !approved },
          { id: 'matches-approved', label: 'Approved', active: approved },
        ]}
      />
      {list.length === 0 ? (
        <Empty
          icon={<Inbox className="h-8 w-8" />}
          title={approved ? 'Nothing in flight' : 'No matches waiting'}
          body={
            approved
              ? 'Approve a match and it appears here until its application is sent.'
              : 'Hunt now to scrape job sites and match them against your CV.'
          }
        />
      ) : (
        <div className="space-y-3">
          {list.map((m) => (
            <MatchCard
              key={m.id}
              match={m}
              onDecision={onDecision}
              onPrepare={onPrepare}
              onReview={() => onSwitch('apps-review')}
              prepared={preparedJobIds.has(m.job_id)}
            />
          ))}
        </div>
      )}
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
  page,
  drafts,
  applications,
  onChanged,
  onSwitch,
  subTabsAlways = false,
}: {
  page: 'apps-review' | 'apps-sent';
  drafts: ApplicationDraft[];
  applications: (Application & { job?: { title: string; company: string | null } })[];
  onChanged: () => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  onSwitch: (v: View) => void;
  subTabsAlways?: boolean;
}) {
  const openDrafts = drafts.filter((d) => d.status !== 'submitted');
  // Accordion: one draft open at a time; a single draft opens by itself
  const [openId, setOpenId] = useState<number | null>(() =>
    openDrafts.length === 1 ? openDrafts[0].id : null
  );
  const draftById = new Map(drafts.map((d) => [d.id, d]));
  const draftFor = (a: (Application & { job?: { title: string; company: string | null } })) =>
    a.draft_id ? draftById.get(a.draft_id) : undefined;
  const subTabs = (
    <SubTabs
      onSwitch={onSwitch}
      forceVisible={subTabsAlways}
      options={[
        { id: 'apps-review', label: 'Review & send', active: page === 'apps-review' },
        { id: 'apps-sent', label: 'Sent', active: page === 'apps-sent' },
      ]}
    />
  );

  if (page === 'apps-sent') {
    return (
      <section>
        <ViewHeader
          title="Sent"
          sub="Everything you've released — by email or through a browser. Open one to re-read the letter or re-download the PDFs."
        />
        {subTabs}
        {applications.length === 0 ? (
          <Empty
            icon={<Send className="h-8 w-8" />}
            title="Nothing sent yet"
            body="Once you approve a draft, it lands here — sent by email or opened in your browser."
          />
        ) : (
          <div className="space-y-3">
            {applications.map((a) => (
              <SentApplicationCard key={a.id} application={a} draft={draftFor(a)} onChanged={onChanged} />
            ))}
          </div>
        )}
      </section>
    );
  }

  // Accordion state lives up top (rules of hooks); only the review page uses it

  return (
    <section>
      <ViewHeader
        title="Review & send"
        sub="AI tailored your CV and cover letter to each approved job. Open one to read and edit it, then send. Nothing goes out without you."
      />
      {subTabs}
      {openDrafts.length === 0 ? (
        <Empty
          icon={<FileText className="h-8 w-8" />}
          title="No drafts waiting"
          body="Approve a match and press “Prepare application” — the AI will tailor your CV and cover letter for that job."
        />
      ) : (
        <div className="space-y-3">
          {openDrafts.map((d) => (
            <DraftCard
              key={d.id}
              draft={d}
              expanded={openId === d.id}
              onToggle={() => setOpenId(openId === d.id ? null : d.id)}
              onChanged={onChanged}
            />
          ))}
        </div>
      )}
    </section>
  );
}

// Retry with visible busy/error feedback — never a silent unhandled rejection
function RetryButton({
  applicationId,
  onChanged,
}: {
  applicationId: number;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="flex items-center gap-2">
      {error && <span className="text-xs text-bad">{error}</span>}
      <button
        onClick={async (e) => {
          e.stopPropagation();
          setBusy(true);
          setError(null);
          try {
            await retryApplication(applicationId);
            await onChanged();
          } catch (err) {
            setError(err instanceof Error ? err.message : 'Retry failed');
          } finally {
            setBusy(false);
          }
        }}
        disabled={busy}
        className="rounded-lg bg-signal px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-signal/90 disabled:opacity-50"
      >
        {busy ? 'Retrying…' : 'Retry'}
      </button>
    </span>
  );
}

// A sent application: header row with status/actions; expands to re-read
// the letter and re-download the PDFs (the draft survives submission).
function SentApplicationCard({
  application,
  draft,
  onChanged,
}: {
  application: Application & { job?: { title: string; company: string | null } };
  draft?: ApplicationDraft;
  onChanged: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const a = application;
  const hasDocuments = Boolean(draft && (draft.cover_letter || draft.tailored_cv));

  return (
    <div className="rounded-xl border border-line bg-surface/80 transition-colors hover:border-line-2">
      <div
        className={cn(
          'flex flex-wrap items-center gap-3 p-4',
          hasDocuments && 'cursor-pointer'
        )}
        onClick={hasDocuments ? () => setExpanded(!expanded) : undefined}
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
          {a.status === 'sent'
            ? 'sent ✓'
            : a.status === 'manual_pending'
              ? 'finish on portal'
              : a.status.replace('_', ' ')}
        </span>
        {a.status === 'manual_pending' && a.apply_url && (
          <a
            href={a.apply_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
          >
            <ExternalLink className="h-4 w-4" /> Open posting
          </a>
        )}
        {a.status === 'failed' && a.method === 'email' && (
          <button
            onClick={async (e) => {
              e.stopPropagation();
              await retryApplication(a.id);
              onChanged();
            }}
            className="rounded-lg bg-signal px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-signal/90"
          >
            Retry
          </button>
        )}
        {hasDocuments && (
          <ChevronDown
            className={cn('h-5 w-5 shrink-0 text-low transition-transform', expanded && 'rotate-180')}
          />
        )}
      </div>

      <AnimatePresence>
        {expanded && hasDocuments && draft && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-line"
          >
            <div className="space-y-4 p-4">
              {draft.cover_letter && (
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                      Cover letter
                    </p>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => window.open(draftCoverLetterPdfUrl(draft.id), '_blank', 'noopener')}
                        className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                      >
                        <Download className="h-3.5 w-3.5" /> PDF
                      </button>
                    </div>
                  </div>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-ink p-3 font-mono text-xs leading-relaxed text-mid">
                    {draft.cover_letter}
                  </pre>
                </div>
              )}
              {draft.tailored_cv && (
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                      CV sent
                    </p>
                    <button
                      onClick={() => window.open(draftCvPdfUrl(draft.id), '_blank', 'noopener')}
                      className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                    >
                      <Download className="h-3.5 w-3.5" /> PDF
                    </button>
                  </div>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-ink p-3 font-mono text-xs leading-relaxed text-mid">
                    {draft.tailored_cv}
                  </pre>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// A single draft: read/edit cover letter + tailored CV, then submit
function DraftCard({
  draft,
  expanded,
  onToggle,
  onChanged,
}: {
  draft: ApplicationDraft;
  expanded: boolean;
  onToggle: () => void;
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
      setSubmitError(apiErrorMessage(err));
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
    <div className="rounded-xl border border-line bg-surface/80 transition-colors hover:border-line-2">
      {/* Header — click to open the review workspace */}
      <div className="flex cursor-pointer items-center gap-3 p-4" onClick={onToggle}>
        <h3 className="min-w-0 flex-1 truncate font-semibold text-hi">
          {job?.title ?? `Job #${draft.job_id}`}
          <span className="ml-2 text-sm font-normal text-low">{job?.company}</span>
        </h3>
        {dirty && (
          <span className="rounded-full bg-signal/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-signal">
            unsaved
          </span>
        )}
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
        <ChevronDown
          className={cn('h-5 w-5 shrink-0 text-low transition-transform', expanded && 'rotate-180')}
        />
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-line"
          >
            <div className="space-y-4 p-4">
              {draft.status === 'drafting' && (
                <p className="flex items-center gap-2 text-sm text-mid" role="status">
                  <Loader2 className="h-4 w-4 animate-spin text-signal" />
                  AI is tailoring your CV and cover letter — this box fills in by itself, usually
                  within a minute.
                </p>
              )}

              {draft.status === 'failed' && draft.error && (
                <p className="text-sm text-bad">{draft.error}</p>
              )}

              {/* What the AI changed */}
              {draft.status === 'ready' && draft.changes_summary.length > 0 && (
                <div className="rounded-lg border border-line bg-ink/60 p-3">
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
          <div>
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
          <div>
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
            <p className="rounded-lg bg-bad/10 p-3 text-sm text-bad" role="alert">
              {submitError}
            </p>
          )}

          {/* Actions */}
          <div className="flex flex-wrap items-center gap-2">
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
          </motion.div>
        )}
      </AnimatePresence>
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
  const [fullName, setFullName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [phone, setPhone] = useState('');
  // Background refresh() replaces the profile object every poll; syncing
  // inputs from it mid-edit wiped what the user was typing. Sync only
  // while the inputs are pristine (first load / after a save).
  const prefsDirty = useRef(false);

  useEffect(() => {
    if (prefsDirty.current) return;
    setPreferredRoles(profile?.preferred_roles?.join(', ') ?? '');
    setExcludeKeywords(profile?.exclude_keywords?.join(', ') ?? '');
    setFullName(profile?.full_name ?? '');
    setContactEmail(profile?.email ?? '');
    setPhone(profile?.phone ?? '');
  }, [profile]);

  const save = async () => {
    setSaving(true);
    try {
      await updateProfile({
        preferred_roles: preferredRoles.split(',').map((s) => s.trim()).filter(Boolean),
        exclude_keywords: excludeKeywords.split(',').map((s) => s.trim()).filter(Boolean),
        full_name: fullName.trim() || undefined,
        email: contactEmail.trim() || undefined,
        phone: phone.trim() || undefined,
      });
      prefsDirty.current = false;
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
                    {profile.remote_only ? ' · remote only' : profile.include_remote ? ' · local + remote' : ' · strictly local'}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-low">Languages</p>
                  <p className="mt-1 text-hi">
                    {profile.languages?.length ? profile.languages.join(' · ') : 'not set — jobs in other languages still pass'}
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
            <h3 className="mb-1 font-semibold text-hi">Contact details</h3>
            <p className="mb-3 text-xs text-low">
              Extracted from your CV — correct anything the AI misread. This name and
              email go on your applications and both PDFs.
            </p>
            <div className="grid gap-4 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">Full name</span>
                <input
                  value={fullName}
                  onChange={(e) => { prefsDirty.current = true; setFullName(e.target.value); }}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">Email</span>
                <input
                  value={contactEmail}
                  onChange={(e) => { prefsDirty.current = true; setContactEmail(e.target.value); }}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">Phone</span>
                <input
                  value={phone}
                  onChange={(e) => { prefsDirty.current = true; setPhone(e.target.value); }}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
            </div>
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
                  onChange={(e) => { prefsDirty.current = true; setPreferredRoles(e.target.value); }}
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
                  onChange={(e) => { prefsDirty.current = true; setExcludeKeywords(e.target.value); }}
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

// ---------------- Settings ----------------

function SettingsView() {
  const [integrations, setIntegrations] = useState<IntegrationsStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setIntegrations(await getIntegrations());
    } catch {
      setIntegrations({ composio: { configured: false, accounts: [] } });
    }
  };

  useEffect(() => {
    load();
  }, []);

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      const { redirect_url } = await connectComposio('gmail');
      window.open(redirect_url, '_blank', 'noopener');
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const composio = integrations?.composio;
  const connected = composio?.accounts.filter((a) => a.status === 'ACTIVE') ?? [];

  return (
    <section className="space-y-6">
      <ViewHeader
        title="Settings"
        sub="Account-level configuration. Integrations connect JobFinderOS to your world."
      />

      <div className="rounded-xl border border-line bg-surface/80 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-semibold text-hi">Composio</h3>
            <p className="mt-1 max-w-md text-sm text-low">
              The integrations layer — connect your Gmail once and JobFinderOS can send
              applications from your own email (coming next), plus any other tool Composio
              offers as the platform grows.
            </p>
          </div>
          {composio?.configured ? (
            <button
              onClick={connect}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              {busy ? 'Opening…' : 'Connect Gmail'}
            </button>
          ) : (
            <span className="rounded-full bg-surface-2 px-2.5 py-1 text-xs text-low">
              not configured
            </span>
          )}
        </div>

        {composio && !composio.configured && (
          <p className="mt-4 rounded-lg bg-bad/10 p-3 text-sm text-hi" role="alert">
            Add <code className="text-signal">COMPOSIO_API_KEY</code> to{' '}
            <code className="text-signal">backend/.env</code> to enable — grab one from your
            Composio dashboard (composio.dev), then restart the backend.
          </p>
        )}

        {connected.length > 0 && (
          <div className="mt-4 space-y-1.5">
            {connected.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="capitalize text-mid">{a.app_name}</span>
                <span className="inline-flex items-center gap-1.5 text-ok">
                  <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden /> connected
                </span>
              </div>
            ))}
          </div>
        )}

        {error && <p className="mt-3 text-sm text-bad">{error}</p>}
      </div>
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
