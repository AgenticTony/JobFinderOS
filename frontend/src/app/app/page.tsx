'use client';

// JobFinderOS — the hunting console.
// Pipeline: scrape -> match vs CV -> approve -> tailor CV & cover letter -> review -> send.
// Shell: left sidebar (nav + live hunt status) on md+, compact top bar on mobile.
// Landing view is the Dashboard: Hunt Pulse funnel + next decisions + status rail.

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertTriangle,
  Check,
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
  RefreshCw,
  Save,
  Send,
  ShieldAlert,
} from 'lucide-react';
import CvUpload from '@/components/CvUpload';
// GA4 funnel events — consent-gated (lib/analytics.ts).
import { track } from '@/lib/analytics';
import MatchCard from '@/components/MatchCard';
import OnboardingWizard from '@/components/OnboardingWizard';
import Sidebar, { NAV, type View } from '@/components/Sidebar';
import HuntPulse from '@/components/HuntPulse';
import {
  attestDraft,
  decideMatch,
  getApplications,
  getDrafts,
  getMatches,
  getPipelineStatus,
  getProfile,
  getProfileStatus,
  api,
  prepareDraft,
  recheckDraft,
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
import {
  apiErrorMessage,
  connectComposio,
  deleteAccount,
  downloadDraftCoverLetterPdf,
  downloadDraftCvPdf,
  exportAccountData,
  getIntegrations,
  getSessionToken,
  logout,
} from '@/lib/api';
import { installGlobalErrorReporter, UNHANDLED_ERROR_EVENT } from '@/lib/globalErrorReporter';
import type { IntegrationsStatus } from '@/types';
import { dicts, type Locale } from '@/i18n/dict';
import { consoleLocale, storeLocale } from '@/i18n/locale';
import { LocaleContext, useLocale, useT } from '@/i18n/LocaleContext';
import { rich } from '@/i18n/rich';

export default function Home() {
  // Console language (2026-10-12): resolved after mount from the saved
  // choice, else the browser (consoleLocale). The static export
  // prerenders 'en', but nothing with text renders before the session
  // check below, so there is no English flash for Swedish users.
  const [locale, setLocaleState] = useState<Locale>('en');
  const setLocale = useCallback((next: Locale) => {
    storeLocale(next);
    setLocaleState(next);
  }, []);
  const localeValue = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  const t = dicts[locale].console;
  // The unhandled-rejection bridge is registered once; it reads the
  // CURRENT language through this ref, not the one at mount.
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
    document.documentElement.lang = locale;
  }, [t, locale]);
  const [view, setView] = useState<View>('dashboard');
  const [status, setStatus] = useState<ProfileStatus | null>(null);
  const [pipeStatus, setPipeStatus] = useState<PipelineStatusResponse | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [matches, setMatches] = useState<Match[]>([]);
  const [drafts, setDrafts] = useState<ApplicationDraft[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [pipelineBusy, setPipelineBusy] = useState(false);
  const [matchPolling, setMatchPolling] = useState(false);
  const [pipelineResult, setPipelineResult] = useState<PipelineRunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);
  // FE-21: the console-level error surface — the same banner pattern the
  // per-card submit/retry errors use (Warning + bad tokens). Mutating
  // actions that used to reject silently (approve/reject, prepare,
  // onboarding finish, and anything that slips past a catch via the
  // unhandledrejection bridge) route their message here.
  const [actionError, setActionError] = useState<string | null>(null);
  // FE-23: set when a full refresh couldn't reach the API — distinguishes
  // "couldn't load" from "loaded, zero items" so the console never renders
  // healthy-but-empty during an outage.
  const [loadFailed, setLoadFailed] = useState(false);
  // P0-4 draft-editor cache: typed cover letters/CVs must survive ANY view
  // switch. The keyed <motion.div> below remounts the whole view subtree on
  // every view/sub-tab change (the transition animation depends on the key),
  // so editor state living inside DraftCard was destroyed on each remount —
  // minutes of typing vanished by clicking a sub-tab, silently. The cache
  // lives HERE, above the keyed container, keyed by draft id. Entries hold
  // ONLY the fields the user actually typed; untouched fields keep deriving
  // from the server draft, which is also the FE-20 pristine-sync: a
  // drafting→ready flip under a mounted card now shows the AI's text with
  // no effect code. An entry existing at all means "dirty"; the entry is
  // deleted once its edits are flushed to the server.
  const [draftEdits, setDraftEdits] = useState<Record<number, DraftEdits>>({});
  const anyDraftDirty = Object.keys(draftEdits).length > 0;
  const editDraft = useCallback((id: number, patch: DraftEdits) => {
    setDraftEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }, []);
  const clearDraftEdits = useCallback((id: number) => {
    setDraftEdits((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);
  // Sidebar rail/expanded choice — persisted so the console opens the way
  // the user left it. Adopted after mount; written only on user toggle
  // (never on mount, so a stray initial render can't clobber the choice).
  const [railCollapsed, setRailCollapsed] = useState(false);
  useEffect(() => {
    setRailCollapsed(localStorage.getItem('jfos-rail-collapsed') === '1');
  }, []);
  // Gate the shell: render nothing but the console ground until the
  // token check has run, so anonymous visitors never see the app flash.
  const [sessionKnown, setSessionKnown] = useState(false);
  // No session -> never signed in here: straight to the create-account
  // form — via replace() so Back from login returns to the page BEFORE
  // /app (the home page), not back here in a redirect loop. Expired
  // sessions still land on plain /login via the 401 interceptor in
  // api.ts. (MIG-WO2: the check is async — the Supabase SDK may be
  // refreshing the token at boot.)
  useEffect(() => {
    let alive = true;
    getSessionToken().then((token) => {
      if (!alive) return;
      const resolved = consoleLocale();
      if (!token) {
        window.location.replace(
          resolved === 'sv' ? '/sv/login?mode=register' : '/login?mode=register'
        );
        return;
      }
      setLocaleState(resolved);
      setSessionKnown(true);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggleRail = () =>
    setRailCollapsed((c) => {
      const next = !c;
      localStorage.setItem('jfos-rail-collapsed', next ? '1' : '0');
      return next;
    });

  // FE-21: bridge global unhandled promise rejections into the same
  // actionError banner the explicit catches use — one error surface, so
  // a forgotten await can never again die as console-only noise while
  // the UI spins or does nothing.
  useEffect(() => {
    installGlobalErrorReporter();
    const onUnhandled = (e: Event) => {
      setActionError(tRef.current.shell.somethingFailed((e as CustomEvent<string>).detail));
    };
    window.addEventListener(UNHANDLED_ERROR_EVENT, onUnhandled);
    return () => window.removeEventListener(UNHANDLED_ERROR_EVENT, onUnhandled);
  }, []);

  // P0-4: leaving a view with unsaved draft edits must be a deliberate
  // act, never a silent loss. window.confirm matches this codebase's
  // weight class (the design system has no dialog component yet). OK
  // switches the view — the edits stay in the cache above until saved
  // or the tab closes; Cancel keeps the user where they are.
  const switchView = useCallback(
    (next: View) => {
      if (next === view) return;
      if (
        Object.keys(draftEdits).length > 0 &&
        !window.confirm(t.shell.unsavedLeave)
      ) {
        return;
      }
      setView(next);
    },
    [view, draftEdits, t]
  );

  // P0-4: typed work must also survive an accidental tab close —
  // beforeunload is the browser's only hook for that, and it fires ONLY
  // while some draft is dirty. (Auto-save deliberately not added: the
  // review marks it optional and it would complicate the save flow.)
  useEffect(() => {
    if (!anyDraftDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Chrome's legacy requirement — the dialog doesn't show without it
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [anyDraftDirty]);

  const refresh = useCallback(async () => {
    const [statusRes, profileRes, pipeRes] = await Promise.allSettled([
      getProfileStatus(),
      getProfile(),
      getPipelineStatus(),
    ]);
    if (statusRes.status === 'fulfilled') setStatus(statusRes.value);
    if (profileRes.status === 'fulfilled') setProfile(profileRes.value);
    if (pipeRes.status === 'fulfilled') setPipeStatus(pipeRes.value);
    // FE-23: the old `.catch(() => [])` mapped an unreachable API to empty
    // lists, so an outage rendered as a healthy-but-empty console whose
    // empty states invited a quota-spending hunt to "fix" it. allSettled
    // keeps the failure signal: keep whatever loads, but say so.
    const [matchRes, draftRes, appsRes] = await Promise.allSettled([
      getMatches({ limit: 200 }),
      getDrafts(),
      getApplications(),
    ]);
    setMatches(matchRes.status === 'fulfilled' ? matchRes.value : []);
    setDrafts(draftRes.status === 'fulfilled' ? draftRes.value : []);
    setApplications(appsRes.status === 'fulfilled' ? appsRes.value : []);
    const anyListFailed =
      matchRes.status === 'rejected' || draftRes.status === 'rejected' || appsRes.status === 'rejected';
    setLoadFailed(anyListFailed);
    setLoading(false);
  }, []);

  // FE-23: the banner's Retry — a fresh full load with the honest loading
  // state, not a silent background refetch.
  const retryLoad = useCallback(() => {
    setLoading(true);
    setLoadFailed(false);
    refresh();
  }, [refresh]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Keep the countdown and source health fresh without hammering the API.
  // When the stats signature changes (a scheduled hunt ran in the background),
  // pull the full lists too — otherwise the cards go stale while the counts
  // update, which reads as "new jobs but nothing new".
  //
  // EGRESS 2026-09-11: an always-open console tab polled this every 60s
  // around the clock — with the backend's then-O(pool) stats query that
  // was the #1 driver of Supabase egress hitting 109% of the free tier.
  // A hidden tab has no viewer: skip its polls entirely and catch up the
  // moment it becomes visible again.
  const lastStatsSig = useRef<string | null>(null);
  const pollStatus = useCallback(() => {
    if (document.hidden) return;
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
  }, [refresh]);

  useEffect(() => {
    const id = setInterval(pollStatus, 60_000);
    const onVisible = () => {
      if (!document.hidden) pollStatus();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [pollStatus]);

  // While background matching runs, poll status + stream fresh matches in
  // (same hidden-tab skip as pollStatus — a backgrounded tab doesn't need
  // the 8s match stream; it catches up on visibility)
  useEffect(() => {
    if (!matchPolling) return;
    const poll = async () => {
      if (document.hidden) return;
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
    };
    const id = setInterval(poll, 8000);
    const onVisible = () => {
      if (!document.hidden) void poll();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [matchPolling, refresh]);

  const handleRunPipeline = async (backfill = false) => {
    setPipelineBusy(true);
    setPipelineResult(null);
    try {
      // 1) Scrape only — fast (~10s), returns immediately with results.
      //    Onboarding passes backfill: the first hunt reads the full
      //    history for the new queries/municipalities, not just the
      //    last day's delta.
      const result = await runPipeline({ match: false, backfill });
      track('hunt_run', { backfill });
      setPipelineResult(result);
      // 2) Kick AI matching into the background and stream results via polling
      const mr = await runMatching();
      if (mr?.status === 'daily_cap_reached' && mr.message) {
        // WO-14: capped for today — a clear notice beats a silent empty
        // queue (and polling would never produce anything new).
        setMatchPolling(false);
        setPipelineResult({
          ...result,
          match: {
            status: 'notice',
            jobs_considered: 0,
            matches_created: 0,
            error: mr.message,
          },
        });
        await refresh();
        return;
      }
      setMatchPolling(true);
      await refresh();
    } catch (err) {
      setPipelineResult({
        scrape: [],
        match: {
          status: 'failed',
          jobs_considered: 0,
          matches_created: 0,
          error: err instanceof Error ? err.message : t.shell.pipelineFailed,
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

  // Show the onboarding wizard until setup is done. The wizard collects
  // the CV itself (step 1), so a brand-new account starts the wizard
  // WITHOUT one — the old flow opened it CV-less and the AI titles step
  // dead-ended (suggest route 400s with no cv_text).
  useEffect(() => {
    if (profile && !profile.onboarded) setShowWizard(true);
  }, [profile?.onboarded]);

  const handleOnboardingComplete = async (payload: OnboardingPayload) => {
    setActionError(null);
    try {
      await saveOnboarding(payload);
    } catch (err) {
      // FE-21: route to the console banner AND rethrow — the wizard modal
      // covers the whole screen, so its own catch (OnboardingWizard finish)
      // shows the inline copy the user actually sees; that catch HANDLES
      // this rethrow, so it never becomes an unhandled rejection. The
      // wizard stays open: the user's picks are not lost.
      setActionError(t.shell.saveSetupFailed(apiErrorMessage(err)));
      throw err;
    }
    track('onboarding_completed');
    setShowWizard(false);
    await refresh();
    handleRunPipeline(true); // first targeted run: deep backfill, straight away
  };

  const handleDecision = async (matchId: number, decision: 'approved' | 'rejected') => {
    setActionError(null);
    try {
      await decideMatch(matchId, decision);
      track('match_decision', { decision });
      await refresh();
    } catch (err) {
      // FE-21: on a cold-starting free-tier backend this rejection was the
      // norm, and the button just did nothing. Surface it in the banner.
      setActionError(t.shell.decisionFailed(decision === 'approved', apiErrorMessage(err)));
    }
  };

  const handlePrepare = async (jobId: number) => {
    setActionError(null);
    try {
      await prepareDraft(jobId); // tailors CV + cover letter (~5-20s)
      switchView('apps-review'); // take the user straight to the review stage
      await refresh();
    } catch (err) {
      // Stay on the current view (switchView above only runs on success,
      // and its P0-4 dirty-confirm is untouched) — dropping the user on an
      // empty review page after a failed prepare would read as data loss.
      setActionError(t.shell.prepareFailed(apiErrorMessage(err)));
    }
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
      onClick={() => handleRunPipeline()}
      disabled={hunting}
      aria-busy={hunting}
      title={t.shell.huntTitle}
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
          {pipelineBusy ? t.shell.hunting : matchPolling ? t.shell.matchingBackground : t.shell.huntNow}
        </span>
      )}
      {compact && <span className="sr-only">{t.shell.huntNowSr}</span>}
    </button>
  );

  const huntsAutomated = huntsAreAutomated(pipeStatus);
  // FE-23: nothing survived the load — if the load ALSO failed, render
  // only the failure banner. The empty-state copy ("Hunt now to…")
  // assumes a working backend; during an outage it invites a
  // quota-spending hunt to "fix" a problem the user cannot fix.
  const nothingLoaded =
    matches.length === 0 && drafts.length === 0 && applications.length === 0 && status === null;

  if (!sessionKnown) {
    return <div className="console-backdrop min-h-dvh bg-ink" />;
  }

  return (
    <LocaleContext.Provider value={localeValue}>
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
        {/* Mobile nav: spread to fill when the labels fit, horizontally
            scrollable when they don't — seven single-word labels
            ("Applications", "Beta feedback") don't fit one 360px row, and
            without basis-auto they crush and bleed into each other. */}
        <nav
          className="no-scrollbar flex overflow-x-auto border-t border-line"
          aria-label={t.shell.mainNav}
        >
          {NAV.map(({ id, label, icon: Icon }) => {
            const active =
              view === id ||
              Boolean(NAV.find((n) => n.id === id)?.children?.some((c) => c.id === view));
            return (
            <button
              key={id}
              onClick={() => switchView(id)}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'relative flex grow basis-auto shrink-0 flex-col items-center gap-0.5 whitespace-nowrap px-2 py-2 text-[10px] font-medium',
                active ? 'text-signal' : 'text-low hover:text-mid'
              )}
            >
              {active && <span className="absolute inset-x-4 top-0 h-0.5 rounded-full bg-signal" />}
              <Icon className="h-[18px] w-[18px]" aria-hidden />
              {t.nav[label]}
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
          onNavigate={switchView}
          pendingCount={pendingCount}
          reviewCount={openDrafts}
          nextRunAt={pipeStatus?.next_run_at ?? null}
          schedulerEnabled={huntsAutomated}
          intervalMinutes={pipeStatus?.scrape_interval_minutes ?? 180}
          profile={profile}
          onEditSetup={() => (profile ? setShowWizard(true) : switchView('profile'))}
          collapsed={railCollapsed}
          onToggleCollapsed={toggleRail}
        >
          {huntButton(false)}
        </Sidebar>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 md:py-8 lg:px-10">
          <div className="mx-auto max-w-5xl">
            {loading ? (
              <div className="flex items-center justify-center py-24 text-low">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" /> {t.shell.warmingUp}
              </div>
            ) : loadFailed && nothingLoaded ? (
              <LoadFailure onRetry={retryLoad} />
            ) : (
              <>
                {loadFailed && (
                  <div className="mb-6">
                    <LoadFailure onRetry={retryLoad} />
                  </div>
                )}
                {actionError && (
                  <div className="mb-6" role="alert">
                    <Warning>{actionError}</Warning>
                  </div>
                )}
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
                    onOpenMatches={() => switchView('matches')}
                    onOpenReview={() => switchView('apps-review')}
                    onOpenSent={() => switchView('apps-sent')}
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
                    onSwitch={switchView}
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
                    onSwitch={switchView}
                    subTabsAlways={railCollapsed}
                    draftEdits={draftEdits}
                    onEditDraft={editDraft}
                    onClearDraftEdits={clearDraftEdits}
                  />
                )}

                {view === 'settings' && <SettingsView />}

                {view === 'feedback' && <FeedbackView />}

                {view === 'profile' && (
                  <ProfileView
                    profile={profile}
                    onUpload={handleUpload}
                    onSaved={refresh}
                    onEditSetup={() => setShowWizard(true)}
                  />
                )}
                </motion.div>
              </>
            )}
          </div>
        </main>
      </div>

      {/* Onboarding wizard — auto-shows until setup is done, re-openable anytime */}
      {showWizard && profile && (
        <OnboardingWizard
          onComplete={handleOnboardingComplete}
          onClose={() => setShowWizard(false)}
          onUploadCv={handleUpload}
          initialHasCv={!!profile.cv_file_name}
          initialLanguages={profile.languages}
          initialIncludeRemote={profile.include_remote || profile.remote_only}
          initialCountry={profile.country ?? ''}
          initialRegion={profile.region ?? ''}
          initialMunicipality={profile.municipality ?? ''}
          initialMunicipalities={
            // LENGTH check, not ??: the API returns [] (never null) for
            // a NULL municipalities column, so ?? never fired and a
            // legacy single-municipality profile prefilled as empty —
            // then saved as explicit whole-region (review regression).
            profile.municipalities?.length
              ? profile.municipalities
              : (profile.municipality ? [profile.municipality] : [])
          }
          initialSearchRadiusKm={profile.search_radius_km ?? 0}
          initialOccupations={profile.occupation_codes ?? []}
          initialQueries={profile.search_queries}
          initialWorkRights={profile.work_rights}
        />
      )}
    </div>
    </LocaleContext.Provider>
  );
}

// ---------------- Dashboard ----------------

// Hunts are automated when the API says so (dev scheduler, or the
// external cron via HUNT_TIMES_UTC) — or when the evidence says so:
// a completed scrape run in the last two hours means SOMETHING is
// scheduling hunts (the Render cron), even when the API can't name
// the next time yet.
function huntsAreAutomated(pipeStatus: PipelineStatusResponse | null): boolean {
  return (
    (pipeStatus?.scheduler_enabled ?? false) ||
    (pipeStatus?.recent_runs ?? []).some(
      (r) =>
        r.status === 'completed' &&
        Date.now() - parseUtcDate(r.started_at).getTime() < 2 * 60 * 60_000
    )
  );
}

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
  const t = useT().console.dashboard;
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
  const huntsAutomated = huntsAreAutomated(pipeStatus);
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
        schedulerEnabled={huntsAutomated}
        intervalMinutes={pipeStatus?.scrape_interval_minutes ?? 180}
        lastHuntAt={lastHuntAt}
        onOpenMatches={onOpenMatches}
        onOpenReview={onOpenReview}
        onOpenSent={onOpenSent}
      />

      {/* minmax(0,1fr) at EVERY width: below xl the single implicit
          auto track let a match card's untruncatable title line size the
          track to its min-content (610px at a 360px viewport — the whole
          page scrolled sideways). A zero floor forces the card to shrink
          and the truncate/ellipsis styles inside to do their job. */}
      <div
        className={cn(
          'grid grid-cols-[minmax(0,1fr)] gap-6',
          hasRailWarnings && 'xl:grid-cols-[minmax(0,1fr)_300px]'
        )}
      >
        {/* Main column — finish what you started, fresh arrivals, then the queue */}
        <div className="space-y-8">
          {finishApplying.length > 0 && (
            <div>
              <div className="mb-3 flex items-baseline justify-between">
                <h2 className="text-lg font-semibold tracking-tight text-hi">
                  {t.finishApplying}
                  <span className="num ml-2 text-sm font-normal text-signal">{finishApplying.length}</span>
                  <span className="ml-2 text-sm font-normal text-low">{t.approvedNotSent}</span>
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
                <h2 className="text-lg font-semibold tracking-tight text-hi">
                  {t.newLast24h}
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
                <h2 className="text-lg font-semibold tracking-tight text-hi">
                  {t.nextDecisions}
                  {decisions.length > 0 && (
                    <span className="num ml-2 text-sm font-normal text-low">{decisions.length}</span>
                  )}
                </h2>
                {pendingMatches.length > 0 && (
                  <button
                    onClick={onOpenMatches}
                    className="text-sm text-signal transition-colors hover:text-signal/80"
                  >
                    {t.viewAllMatches}
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
                      ? t.scrapingTitle
                      : matchPolling
                        ? t.rankingTitle
                        : t.nothingElseTitle
                  }
                  body={
                    pipelineBusy
                      ? t.scrapingBody
                      : matchPolling
                        ? t.rankingBody
                        : t.nothingElseBody
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
                {rich(t.noCv, 'font-bold')}
              </Warning>
            )}
            {profileStatus && !profileStatus.ai_enabled && (
              <Warning>
                <Cpu className="mr-1.5 inline h-4 w-4" />
                {t.aiUnavailable}
              </Warning>
            )}
            {matchFailed && pipelineResult?.match?.error && (
              <Warning>{t.lastHuntFailed(pipelineResult.match.error)}</Warning>
            )}
            {/* WO-14: informational hunt notices — the daily trial cap and
                the scrape cooldown ("no new jobs since HH:MM") are honest
                "nothing to do right now" states, not failures, so they get
                this amber status line rather than the red failure banner. */}
            {(pipelineResult?.match?.status === 'notice' && pipelineResult.match.error) ||
              (pipelineResult?.scrape?.length &&
                pipelineResult.scrape.every((s) => s.status === 'skipped_cooldown') &&
                pipelineResult.scrape[0].error) ? (
              <div
                className="rounded-lg border border-signal/30 bg-signal/10 p-3 text-sm text-hi"
                role="status"
              >
                {pipelineResult?.match?.status === 'notice'
                  ? pipelineResult.match.error
                  : pipelineResult?.scrape?.[0]?.error}
              </div>
            ) : null}
            {/* Email-apply config is deliberately NOT banner-warned: the platform's
                email path is Composio connected-email (see CLAUDE.md decided
                architecture), and the draft card already shows a precise error at
                submit time if Resend keys are missing. Warn at the action, not
                permanently. */}
            {matchPolling && (
              <p className="text-xs text-signal" role="status">
                {t.matchingNow}
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
  const t = useT().console.matches;
  // Approved page = in flight: approved and not yet sent. Once sent, the
  // job's record lives in Applications → Sent — it leaves Matches for good.
  const list = matches.filter((m) =>
    approved ? m.decision === 'approved' && !appliedJobIds.has(m.job_id) : !m.decision
  );

  return (
    <section>
      {approved ? (
        <ViewHeader title={t.approvedTitle} sub={t.approvedSub} />
      ) : (
        <ViewHeader title={t.awaitingTitle} sub={t.awaitingSub} />
      )}
      <SubTabs
        onSwitch={onSwitch}
        forceVisible={subTabsAlways}
        options={[
          { id: 'matches', label: t.tabAwaiting, active: !approved },
          { id: 'matches-approved', label: t.tabApproved, active: approved },
        ]}
      />
      {list.length === 0 ? (
        <Empty
          icon={<Inbox className="h-8 w-8" />}
          title={approved ? t.emptyApprovedTitle : t.emptyAwaitingTitle}
          body={approved ? t.emptyApprovedBody : t.emptyAwaitingBody}
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
  draftEdits,
  onEditDraft,
  onClearDraftEdits,
}: {
  page: 'apps-review' | 'apps-sent';
  drafts: ApplicationDraft[];
  applications: (Application)[];
  onChanged: () => Promise<void>;
  onPrepare: (jobId: number) => Promise<void>;
  onSwitch: (v: View) => void;
  subTabsAlways?: boolean;
  draftEdits: Record<number, DraftEdits>;
  onEditDraft: (id: number, patch: DraftEdits) => void;
  onClearDraftEdits: (id: number) => void;
}) {
  const t = useT().console.apps;
  const openDrafts = drafts.filter((d) => d.status !== 'submitted');
  // Accordion: one draft open at a time; a single draft opens by itself
  const [openId, setOpenId] = useState<number | null>(() =>
    openDrafts.length === 1 ? openDrafts[0].id : null
  );
  const draftById = new Map(drafts.map((d) => [d.id, d]));
  const draftFor = (a: (Application)) =>
    a.draft_id ? draftById.get(a.draft_id) : undefined;
  const subTabs = (
    <SubTabs
      onSwitch={onSwitch}
      forceVisible={subTabsAlways}
      options={[
        { id: 'apps-review', label: t.tabReview, active: page === 'apps-review' },
        { id: 'apps-sent', label: t.tabSent, active: page === 'apps-sent' },
      ]}
    />
  );

  if (page === 'apps-sent') {
    return (
      <section>
        <ViewHeader title={t.sentTitle} sub={t.sentSub} />
        {subTabs}
        {applications.length === 0 ? (
          <Empty
            icon={<Send className="h-8 w-8" />}
            title={t.emptySentTitle}
            body={t.emptySentBody}
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
      <ViewHeader title={t.reviewTitle} sub={t.reviewSub} />
      {subTabs}
      {openDrafts.length === 0 ? (
        <Empty
          icon={<FileText className="h-8 w-8" />}
          title={t.emptyReviewTitle}
          body={t.emptyReviewBody}
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
              edits={draftEdits[d.id]}
              onEdit={(patch) => onEditDraft(d.id, patch)}
              onClearEdits={() => onClearDraftEdits(d.id)}
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
  const t = useT().console.apps;
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
            setError(err instanceof Error ? err.message : t.retryFailed);
          } finally {
            setBusy(false);
          }
        }}
        disabled={busy}
        className="rounded-lg bg-signal px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-signal/90 disabled:opacity-50"
      >
        {busy ? t.retrying : t.retry}
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
  application: Application;
  draft?: ApplicationDraft;
  onChanged: () => Promise<void>;
}) {
  const tc = useT().console;
  const t = tc.apps;
  const [expanded, setExpanded] = useState(false);
  // FE-9: the header is keyboard-operable (role="button" — a real <button>
  // is impossible here because the header legitimately CONTAINS interactive
  // controls: the "Open posting" link and the Retry button; a <button>
  // cannot nest them). aria-expanded/aria-controls wire the header to the
  // expandable panel below.
  const panelId = useId();
  // FE-22: download failures used to vanish into .catch(console.error) —
  // the user clicked "PDF" and nothing happened. Same inline-error
  // pattern as the retry button next to it.
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const a = application;
  const hasDocuments = Boolean(draft && (draft.cover_letter || draft.tailored_cv));

  const download = async (run: () => Promise<void>) => {
    setDownloadError(null);
    try {
      await run();
    } catch (err) {
      setDownloadError(apiErrorMessage(err));
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface/80 transition-colors hover:border-line-2">
      <div
        role={hasDocuments ? 'button' : undefined}
        tabIndex={hasDocuments ? 0 : undefined}
        aria-expanded={hasDocuments ? expanded : undefined}
        aria-controls={hasDocuments ? panelId : undefined}
        className={cn(
          'flex flex-wrap items-center gap-3 p-4',
          hasDocuments && 'cursor-pointer'
        )}
        onClick={hasDocuments ? () => setExpanded(!expanded) : undefined}
        onKeyDown={
          hasDocuments
            ? (e) => {
                // Inner controls (Retry, the posting link) keep their own
                // keys — only act when the header itself has focus.
                if (e.target !== e.currentTarget) return;
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault(); // Space must not scroll the page
                  setExpanded(!expanded);
                }
              }
            : undefined
        }
      >
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-hi">
            {a.job?.title ?? t.jobFallback(a.job_id)}
          </p>
          <p className="text-sm text-low">
            {a.job?.company} · {t.method[a.method] ?? a.method} · {timeAgo(a.created_at, tc.time)}
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
          {t.status[a.status] ?? a.status.replace('_', ' ')}
        </span>
        {a.status === 'manual_pending' && a.apply_url && (
          <a
            href={a.apply_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
          >
            <ExternalLink className="h-4 w-4" /> {t.openPosting}
          </a>
        )}
        {a.status === 'failed' && a.method === 'email' && (
          <RetryButton applicationId={a.id} onChanged={onChanged} />
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
            id={panelId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-line"
          >
            <div className="space-y-4 p-4">
              {downloadError && (
                <p className="rounded-lg bg-bad/10 p-3 text-sm text-bad" role="alert">
                  {downloadError}
                </p>
              )}
              {draft.cover_letter && (
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                      {t.coverLetter}
                    </p>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => download(() => downloadDraftCoverLetterPdf(draft.id))}
                        className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                      >
                        <Download className="h-3.5 w-3.5" /> {t.pdf}
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
                      {t.cvSent}
                    </p>
                    <button
                      onClick={() => download(() => downloadDraftCvPdf(draft.id))}
                      className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                    >
                      <Download className="h-3.5 w-3.5" /> {t.pdf}
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

// Unsaved draft edits, hoisted above the keyed view container (P0-4).
// Only fields the user actually typed are present — presence of an entry
// (or a field in it) means "touched"; absence means pristine, and the
// displayed value derives straight from the server draft.
type DraftEdits = { coverLetter?: string; tailoredCv?: string };

// FE-20 empty-clobber guard: the PUT sends exactly what the user sees —
// EXCEPT a field they never touched that was empty on the server, which
// is omitted entirely (the backend's save_draft_edits treats an omitted
// field as "unchanged", so AI text that arrived after mount can never be
// overwritten with the empty string the card booted with). A field the
// user deliberately cleared ('') is still sent — that's an intentional
// edit, not a clobber.
function draftSavePayload(
  draft: ApplicationDraft,
  edits: DraftEdits | undefined,
): { cover_letter?: string; tailored_cv?: string } {
  const payload: { cover_letter?: string; tailored_cv?: string } = {};
  if (edits?.coverLetter !== undefined || draft.cover_letter) {
    payload.cover_letter = edits?.coverLetter ?? draft.cover_letter ?? '';
  }
  if (edits?.tailoredCv !== undefined || draft.tailored_cv) {
    payload.tailored_cv = edits?.tailoredCv ?? draft.tailored_cv ?? '';
  }
  return payload;
}

// A single draft: read/edit cover letter + tailored CV, then submit
function DraftCard({
  draft,
  expanded,
  onToggle,
  onChanged,
  edits,
  onEdit,
  onClearEdits,
}: {
  draft: ApplicationDraft;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => Promise<void>;
  /** Unsaved local edits (hoisted cache, P0-4); undefined = pristine */
  edits: DraftEdits | undefined;
  onEdit: (patch: DraftEdits) => void;
  onClearEdits: () => void;
}) {
  // P0-4 + FE-20: the editor's text no longer lives in component state.
  // It derives from the hoisted edits cache when the user typed, else
  // from the server draft — so (a) a remount (any view/sub-tab switch)
  // can never lose typed text, and (b) a drafting→ready flip under a
  // mounted card populates the fields by itself: pristine values always
  // mirror the server, the same pristine-sync idea ProfileView uses,
  // achieved structurally instead of via an effect. Only transient
  // busy/error UI state remains local.
  const tc = useT().console;
  const t = tc.draft;
  const coverLetter = edits?.coverLetter ?? draft.cover_letter ?? '';
  const tailoredCv = edits?.tailoredCv ?? draft.tailored_cv ?? '';
  const dirty = edits !== undefined;
  const [busy, setBusy] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // FE-9: the accordion header is a real <button> (keyboard: Enter/Space
  // toggle natively) wired to its panel via aria-expanded/aria-controls.
  // The dirty chip / confirm flow (P0-4) is untouched — only the header's
  // element type changed.
  const panelId = useId();

  const job = draft.job;
  const canEmail = Boolean(job?.application_email);

  // WO-23 blocked-draft recovery. The editor gate is the GUARD BLOCK
  // (documents present), not status==='failed' — a timeout/malformed
  // failure has no documents to edit and shows only Try again.
  const hasDocs = Boolean(draft.cover_letter || draft.tailored_cv);
  const guardBlocked = draft.status === 'failed' && draft.fabrication_blocked && hasDocs;
  const showEditor = draft.status === 'ready' || guardBlocked;
  // Mirrors the backend's _resolves_finding (round 5): a Layer A value
  // resolves only the SAME use (value AND sentence) — the value's other
  // uses, in sentences the user never saw, stay flagged. Judge findings
  // resolve by value (unique). Variants resolve within their shared
  // sentence by containment — containment alone was the whole-draft
  // override (R2).
  const covers = (kind: string, value: string, context: string) => {
    const v = value.trim().toLowerCase();
    return (draft.fabrication_attested ?? []).some((a) => {
      const c = a.claim.trim().toLowerCase();
      if (!c || !v) return false;
      if (kind === 'judge') return c === v;
      if (c === v) return (a.context ?? '') === context;
      return Boolean(a.context) && a.context === context && (c.includes(v) || v.includes(c));
    });
  };
  const unresolvedHigh = (draft.fabrication_findings ?? []).filter(
    (f) => f.tier === 'high' && !covers(f.kind, f.value, f.context)
  );
  const advisoryFindings = (draft.fabrication_findings ?? []).filter(
    (f) => f.tier === 'advisory'
  );

  const save = async () => {
    setBusy('save');
    try {
      await updateDraft(draft.id, draftSavePayload(draft, edits));
      onClearEdits();
      await onChanged();
    } catch (err) {
      // FE-21: a failed save used to reject through the onClick into an
      // unhandled rejection. The edits cache stays intact (P0-4): the
      // "unsaved" badge remains and nothing typed is lost — only the
      // error panel appears, same surface submit uses.
      setSubmitError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const submit = async (method: 'email' | 'browser') => {
    setSubmitError(null);
    setBusy(`submit-${method}`);
    try {
      if (dirty) {
        await updateDraft(draft.id, draftSavePayload(draft, edits));
        onClearEdits();
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

  // WO-23: run the guard over the current text — flush pending edits
  // first (same contract as submit): the check must see what the user
  // sees, not the last saved version.
  const recheck = async () => {
    setSubmitError(null);
    setBusy('recheck');
    try {
      if (dirty) {
        await updateDraft(draft.id, draftSavePayload(draft, edits));
        onClearEdits();
      }
      await recheckDraft(draft.id);
      await onChanged();
    } catch (err) {
      setSubmitError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  // WO-23: per-claim "This is true — keep it". When no unresolved claim
  // remains the backend flips the draft to ready — no further AI call.
  // Round 4: confirming resolves THIS draft only — saveToProfile is
  // always false from our UI. Per-claim profile saving had no safe form
  // (bare atom: any future "40%" passes Layer A; whole sentence: its
  // unconfirmed claims ride along). Permanent vouching goes through the
  // profile editor — typed, or the explicit whole-sentence action below.
  const attest = async (claim: string, context?: string) => {
    setSubmitError(null);
    setBusy(`attest:${claim}`);
    try {
      await attestDraft(draft.id, claim, false, undefined, context);
      await onChanged();
    } catch (err) {
      setSubmitError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  // Round 4: saving the finding's FULL sentence is its own explicit
  // action — the confirm dialog shows all of it, it saves through the
  // profile-editor channel (the user-entered-facts surface), and the
  // flow COMPLETES: a re-check runs right after, so the claim resolves
  // (the sentence is now in the guard's source) instead of leaving the
  // draft blocked with the claim still listed.
  const saveSentence = async (sentence: string) => {
    if (!window.confirm(t.confirmSentence(sentence))) {
      return;
    }
    setSubmitError(null);
    setBusy('save-sentence');
    try {
      const current = await getProfile();
      const existing = current?.vouched_facts ?? [];
      if (!existing.includes(sentence)) {
        await updateProfile({ vouched_facts: [...existing, sentence] });
      }
      if (dirty) {
        await updateDraft(draft.id, draftSavePayload(draft, edits));
        onClearEdits();
      }
      await recheckDraft(draft.id);
      await onChanged();
    } catch (err) {
      setSubmitError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  // Every failed draft gets the way out that already exists in the API:
  // prepareDraft regenerates a failed draft's documents without the
  // flagged claims.
  const regenerate = async () => {
    setSubmitError(null);
    setBusy('regenerate');
    try {
      await prepareDraft(draft.job_id, true);
      onClearEdits();
      await onChanged();
    } catch (err) {
      setSubmitError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  // Downloads always reflect saved content — flush pending edits first.
  // FE-21/FE-22: failures (API down mid-flush, blob hand-off refused)
  // surface in the card's error panel instead of dying as unhandled
  // rejections while the click silently does nothing.
  const download = async (kind: 'cover-letter' | 'cv') => {
    setSubmitError(null);
    try {
      if (dirty) {
        await updateDraft(draft.id, draftSavePayload(draft, edits));
        onClearEdits();
      }
      if (kind === 'cover-letter') {
        await downloadDraftCoverLetterPdf(draft.id);
      } else {
        await downloadDraftCvPdf(draft.id);
      }
    } catch (err) {
      setSubmitError(apiErrorMessage(err));
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface/80 transition-colors hover:border-line-2">
      {/* Header — click (or Enter/Space) to open the review workspace.
          FE-9: real <button> so keyboard users can operate the accordion;
          Tailwind's preflight resets buttons to inherit font/color and a
          transparent background, so rendering matches the old <div>. */}
      <button
        type="button"
        className="flex w-full cursor-pointer items-center gap-3 p-4 text-left"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={panelId}
      >
        <h3 className="min-w-0 flex-1 truncate font-semibold text-hi">
          {job?.title ?? tc.apps.jobFallback(draft.job_id)}
          <span className="ml-2 text-sm font-normal text-low">{job?.company}</span>
        </h3>
        {dirty && (
          <span className="rounded-full bg-signal/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-signal">
            {t.unsaved}
          </span>
        )}
        {draft.status === 'drafting' && (
          <span className="inline-flex items-center rounded-full bg-surface-2 px-2.5 py-1 text-xs text-mid">
            <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> {t.tailoring}
          </span>
        )}
        {draft.status === 'ready' && (
          <span className="rounded-full bg-signal/15 px-2.5 py-1 text-xs text-signal">
            {t.ready}
          </span>
        )}
        {draft.status === 'failed' && (
          <span className="rounded-full bg-bad/15 px-2.5 py-1 text-xs text-bad">{t.failed}</span>
        )}
        <ChevronDown
          className={cn('h-5 w-5 shrink-0 text-low transition-transform', expanded && 'rotate-180')}
        />
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            id={panelId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-line"
          >
            <div className="space-y-4 p-4">
              {draft.status === 'drafting' && (
                <p className="flex items-center gap-2 text-sm text-mid" role="status">
                  <Loader2 className="h-4 w-4 animate-spin text-signal" />
                  {t.tailoringBody}
                </p>
              )}

              {draft.status === 'failed' && draft.error && (
                <p className="text-sm text-bad">{draft.error}</p>
              )}

              {/* WO-23: why this was blocked — each flagged claim with its
                  sentence, resolved per-claim: fix in the text + Check
                  again, or "This is true — keep it". Never a whole-draft
                  override. */}
              {guardBlocked && unresolvedHigh.length > 0 && (
                <div className="rounded-lg border border-bad/30 bg-bad/5 p-3">
                  <p className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-bad">
                    <ShieldAlert className="h-3.5 w-3.5" /> {t.whyBlocked}
                  </p>
                  <ul className="space-y-2.5">
                    {unresolvedHigh.map((f, i) => {
                      // Round 4: confirming resolves THIS draft only —
                      // no profile write. The full sentence (shown
                      // untruncated above) is the separate permanent
                      // option, and it completes the flow with a re-check.
                      const sentence = (f.context || '').trim();
                      const sentenceSaveable = sentence
                        && sentence !== f.value.trim() && sentence.length <= 200;
                      return (
                      <li key={i} className="text-sm text-mid">
                        • <span className="font-medium text-hi">{f.value}</span>
                        {f.context ? (
                          <span className="text-low"> — “{f.context}”</span>
                        ) : null}
                        <div className="mt-1.5 flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={() => attest(f.value, f.context)}
                            disabled={busy !== null}
                            className="inline-flex items-center gap-1 rounded-lg border border-bad/40 px-2.5 py-1 text-xs text-mid transition-colors hover:border-bad hover:text-hi disabled:opacity-40"
                          >
                            <Check className="h-3 w-3" />
                            {busy === `attest:${f.value}` ? t.confirming : t.thisIsTrue}
                          </button>
                        </div>
                        {sentenceSaveable && (
                          <button
                            type="button"
                            onClick={() => saveSentence(sentence)}
                            disabled={busy !== null}
                            className="mt-1 text-xs text-low underline underline-offset-2 transition-colors hover:text-mid disabled:opacity-40"
                            title={t.saveSentenceTitle}
                          >
                            {busy === 'save-sentence' ? t.savingSentence : t.saveSentence}
                          </button>
                        )}
                      </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {/* WO-23: a non-guard failure (timeout, malformed response,
                  no documents) has nothing to edit — only Try again. */}
              {draft.status === 'failed' && !guardBlocked && (
                <div>
                  <button
                    type="button"
                    onClick={regenerate}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3.5 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi disabled:opacity-40"
                  >
                    <RefreshCw className={cn('h-4 w-4', busy === 'regenerate' && 'animate-spin')} />
                    {busy === 'regenerate' ? t.retrying : t.tryAgain}
                  </button>
                </div>
              )}

              {/* WO-01 fabrication guard: advisory findings (technology-class
                  check) — flagged for REVIEW, never auto-acted on.
                  WO-23 sweep: the column can also carry tier:'high' items
                  (blocked/recovered history) — the advisory panel shows
                  ONLY tier:'advisory'; attested claims render separately. */}
              {draft.status === 'ready' && advisoryFindings.length > 0 && (
                <div className="rounded-lg border border-signal/30 bg-signal/5 p-3">
                  <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-signal">
                    {t.verifyBefore}
                  </p>
                  <ul className="space-y-1">
                    {advisoryFindings.map((f, i) => (
                      <li key={i} className="text-sm text-mid">
                        • <span className="font-medium text-hi">{f.value}</span>
                        {f.context ? (
                          <span className="text-low"> — “{f.context.slice(0, 120)}”</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* WO-23: what the user personally confirmed on this draft —
                  the counterpart of the advisory panel above. */}
              {draft.status === 'ready' && (draft.fabrication_attested?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-line bg-surface-2/50 p-3">
                  <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                    {t.confirmedByYou}
                  </p>
                  <ul className="space-y-1">
                    {draft.fabrication_attested.map((a, i) => (
                      <li key={i} className="text-sm text-mid">
                        • <span className="font-medium text-hi">{a.claim}</span>
                        {a.saved_to_profile ? (
                          <span className="text-low">{t.addedToProfile}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* What the AI changed */}
              {draft.status === 'ready' && draft.changes_summary.length > 0 && (
                <div className="rounded-lg border border-line bg-ink/60 p-3">
                  <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                    {t.aiChanged}
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

              {showEditor && (
                <>
          {/* Cover letter editor */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                {t.coverLetterLabel}
              </p>
              <div className="flex items-center gap-3">
                <button
                  onClick={copyCoverLetter}
                  className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                >
                  <Copy className="h-3.5 w-3.5" /> {t.copy}
                </button>
                <button
                  onClick={() => download('cover-letter')}
                  className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
                >
                  <Download className="h-3.5 w-3.5" /> {tc.apps.pdf}
                </button>
              </div>
            </div>
            <textarea
              value={coverLetter}
              onChange={(e) => onEdit({ coverLetter: e.target.value })}
              rows={10}
              className="w-full rounded-lg border border-line bg-ink p-3 font-mono text-sm leading-relaxed text-hi outline-none transition-colors focus:border-signal"
            />
          </div>

          {/* Tailored CV editor */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                {t.tailoredCvLabel}
              </p>
              <button
                onClick={() => download('cv')}
                className="inline-flex items-center gap-1 text-xs text-low transition-colors hover:text-mid"
              >
                <Download className="h-3.5 w-3.5" /> {tc.apps.pdf}
              </button>
            </div>
            <textarea
              value={tailoredCv}
              onChange={(e) => onEdit({ tailoredCv: e.target.value })}
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
              <Save className="h-4 w-4" /> {busy === 'save' ? t.saving : dirty ? t.saveChanges : t.saved}
            </button>

            {/* WO-23: the recovery actions — the primary exit for a
                blocked draft. Check again runs the shared guard on the
                saved text; Regenerate rewrites without the claims. */}
            {guardBlocked && (
              <>
                <button
                  onClick={recheck}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
                >
                  <ShieldAlert className="h-4 w-4" />
                  {busy === 'recheck' ? t.checking : t.checkAgain}
                </button>
                <button
                  onClick={regenerate}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3.5 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi disabled:opacity-40"
                >
                  <RefreshCw className={cn('h-4 w-4', busy === 'regenerate' && 'animate-spin')} />
                  {busy === 'regenerate' ? t.regenerating : t.regenerate}
                </button>
              </>
            )}

            {draft.status === 'ready' && (
              <>
            <span className="mx-1 h-6 w-px bg-line" />

            {canEmail ? (
              <span
                className="inline-flex items-center gap-1.5 rounded-lg border border-line px-4 py-2 text-sm text-low"
                title={t.emailReturnsTitle}
              >
                <Mail className="h-4 w-4" />
                {t.emailReturns}
              </span>
            ) : (
              <span className="text-xs text-low">{t.noEmail}</span>
            )}
            <button
              onClick={() => submit('browser')}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              <MousePointerClick className="h-4 w-4" />
              {busy === 'submit-browser' ? t.opening : t.applyBrowser}
            </button>
              </>
            )}
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

// The work-rights codes the profile select offers, in display order
// (mirrors backend WORK_RIGHTS_VALUES). Labels: console.workRights.
const PROFILE_WORK_RIGHTS = [
  'citizen_or_pr',
  'permanent_resident',
  'eu_right',
  'needs_sponsorship',
  'prefer_not_say',
] as const;

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
  const tc = useT().console;
  const t = tc.profile;
  const [saving, setSaving] = useState(false);
  // FE-21: profile save used to reject through the onClick silently —
  // the button spun down and the inputs looked saved. Same panel pattern
  // as DraftCard's submitError.
  const [saveError, setSaveError] = useState<string | null>(null);
  const [preferredRoles, setPreferredRoles] = useState('');
  const [excludeKeywords, setExcludeKeywords] = useState('');
  // WO-23: newline-separated — facts contain commas ("Stockholm, Sweden")
  const [vouchedFacts, setVouchedFacts] = useState('');
  const [workRights, setWorkRights] = useState('prefer_not_say');
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
    setVouchedFacts(profile?.vouched_facts?.join('\n') ?? '');
    setWorkRights(profile?.work_rights ?? 'prefer_not_say');
    setFullName(profile?.full_name ?? '');
    setContactEmail(profile?.email ?? '');
    setPhone(profile?.phone ?? '');
  }, [profile]);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await updateProfile({
        preferred_roles: preferredRoles.split(',').map((s) => s.trim()).filter(Boolean),
        exclude_keywords: excludeKeywords.split(',').map((s) => s.trim()).filter(Boolean),
        vouched_facts: vouchedFacts.split('\n').map((s) => s.trim()).filter(Boolean),
        work_rights: workRights,
        full_name: fullName.trim() || undefined,
        email: contactEmail.trim() || undefined,
        phone: phone.trim() || undefined,
      });
      prefsDirty.current = false;
      await onSaved();
    } catch (err) {
      // Keep prefsDirty true: the pristine-sync effect must not clobber
      // the user's unsaved input with the stale server profile.
      setSaveError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-6">
      <ViewHeader title={t.title} sub={t.sub} />
      <CvUpload onUploaded={onUpload} hasExistingCv={!!profile?.cv_file_name} />

      {profile && (
        <>
          {/* Search setup — the onboarding result, always visible/editable */}
          <div className="rounded-xl border border-line bg-surface/80 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-semibold text-hi">{t.setupTitle}</h3>
              <button
                onClick={onEditSetup}
                className="rounded-lg border border-line px-3 py-1.5 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
              >
                {t.editSetup}
              </button>
            </div>
            {profile.onboarded ? (
              <div className="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-low">{t.where}</p>
                  <p className="mt-1 text-hi">
                    {profile.country ? (tc.countries[profile.country] ?? profile.country) : ''}
                    {profile.municipality ? ` · ${profile.municipality}` : profile.region ? ` · ${profile.region}` : ''}
                    {` · ${profile.remote_only ? t.remoteOnly : profile.include_remote ? t.localRemote : t.strictlyLocal}`}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-low">{t.languages}</p>
                  <p className="mt-1 text-hi">
                    {profile.languages?.length
                      ? profile.languages.map((l) => tc.languageNames[l] ?? l).join(' · ')
                      : t.languagesUnset}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.14em] text-low">
                    {t.huntingFor(profile.search_queries.length)}
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
                {t.notSetUp}
              </p>
            )}
          </div>

          <div className="rounded-xl border border-line bg-surface/80 p-5">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold text-hi">{profile.full_name ?? t.yourProfile}</h2>
              {profile.professional_title && (
                <span className="rounded-full border border-line bg-surface-2 px-2.5 py-0.5 text-sm text-mid">
                  {profile.professional_title}
                </span>
              )}
              {/* WO-01 review r5: the bare years badge is gone — a flat
                  "20 yrs exp" under an aspirational title strips the CV's
                  domain qualifier ("20 years in regulated operations"),
                  the same flattening removed from the model's input. The
                  CV text itself (shown below) states it truthfully. */}
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
            <h3 className="mb-1 font-semibold text-hi">{t.contactTitle}</h3>
            <p className="mb-3 text-xs text-low">
              {t.contactBody}
            </p>
            <div className="grid gap-4 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">{t.fullName}</span>
                <input
                  value={fullName}
                  onChange={(e) => { prefsDirty.current = true; setFullName(e.target.value); }}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">{t.email}</span>
                <input
                  value={contactEmail}
                  onChange={(e) => { prefsDirty.current = true; setContactEmail(e.target.value); }}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">{t.phone}</span>
                <input
                  value={phone}
                  onChange={(e) => { prefsDirty.current = true; setPhone(e.target.value); }}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
            </div>
          </div>

          <div className="rounded-xl border border-line bg-surface/80 p-5">
            <h3 className="mb-3 font-semibold text-hi">{t.prefsTitle}</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                  {t.preferredRoles}
                </span>
                <input
                  value={preferredRoles}
                  onChange={(e) => { prefsDirty.current = true; setPreferredRoles(e.target.value); }}
                  placeholder={t.preferredRolesPh}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                  {t.exclude}
                </span>
                <input
                  value={excludeKeywords}
                  onChange={(e) => { prefsDirty.current = true; setExcludeKeywords(e.target.value); }}
                  placeholder={t.excludePh}
                  className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                />
              </label>
            </div>

            {/* WO-19 part B: the eligibility gate's answer — changes which
                postings are hidden (citizenship/clearance for sponsorship
                seekers) and what the tailor may state. */}
            <div className="mt-4">
              <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                {t.workRights}
              </span>
              <select
                value={workRights}
                onChange={(e) => { prefsDirty.current = true; setWorkRights(e.target.value); }}
                className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors focus:border-signal"
              >
                {PROFILE_WORK_RIGHTS.map((v) => (
                  <option key={v} value={v}>
                    {tc.workRights[v]}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-low">
                {t.workRightsNote}
              </p>
            </div>

            {/* WO-23 Part B: "Things I can vouch for" — one fact per line.
                These reach BOTH the AI (it may use them) and the guard
                (it accepts them), so a true-but-not-on-the-CV fact never
                blocks again. */}
            <div className="mt-4">
              <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                {t.vouched}
              </span>
              <textarea
                value={vouchedFacts}
                onChange={(e) => { prefsDirty.current = true; setVouchedFacts(e.target.value); }}
                rows={3}
                placeholder={t.vouchedPh}
                className="w-full rounded-lg border border-line bg-ink p-3 font-mono text-sm leading-relaxed text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
              />
              <p className="mt-1 text-xs text-low">
                {t.vouchedNote}
              </p>
            </div>
            <button
              onClick={save}
              disabled={saving}
              className="mt-4 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              {saving ? t.saving : t.savePrefs}
            </button>
            {saveError && (
              <p className="mt-3 rounded-lg bg-bad/10 p-3 text-sm text-bad" role="alert">
                {saveError}
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

// ---------------- Settings ----------------

// OPS-6: the rights surface. The privacy notice (/privacy) and the
// point-of-collection panels point here for export and erasure — this
// card is what makes those claims real. Copy must stay in sync with the
// backend's behaviour (backend/app/api/v1/account.py): erasure removes
// the profile + CV file, matches, drafts, applications and the account;
// job postings (shared scraped data) stay.
function YourDataCard() {
  const t = useT().console.data;
  const { locale } = useLocale();
  const [busy, setBusy] = useState<'export' | 'delete' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const exportData = async () => {
    setBusy('export');
    setError(null);
    try {
      await exportAccountData();
    } catch (err) {
      setError(t.exportFailed(apiErrorMessage(err)));
    } finally {
      setBusy(null);
    }
  };

  const erase = async () => {
    // Same weight class as the P0-4 unsaved-edits confirm — the design
    // system has no dialog component and this destroys everything.
    const confirmed = window.confirm(t.deleteConfirm);
    if (!confirmed) return;
    setBusy('delete');
    setError(null);
    try {
      await deleteAccount();
      // The session is dead the instant the account is gone (Supabase
      // identity deleted server-side). Sign out and leave to the landing
      // page (not /login — there is nothing left to sign in to, and the
      // background pollers would 401-redirect anyway).
      await logout();
      window.location.replace(locale === 'sv' ? '/sv' : '/');
    } catch (err) {
      setError(t.deleteFailed(apiErrorMessage(err)));
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface/80 p-5">
      <h3 className="font-semibold text-hi">{t.title}</h3>
      <p className="mt-1 max-w-md text-sm text-low">{t.body}</p>
      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <button
          onClick={exportData}
          disabled={busy !== null}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3.5 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi disabled:opacity-40"
        >
          <Download className="h-4 w-4" />
          {busy === 'export' ? t.exporting : t.export}
        </button>
        <button
          onClick={erase}
          disabled={busy !== null}
          className="inline-flex items-center gap-1.5 rounded-lg border border-bad/40 px-3.5 py-2 text-sm text-bad transition-colors hover:bg-bad/10 disabled:opacity-40"
        >
          {busy === 'delete' ? t.deleting : t.delete}
        </button>
        <a
          href={locale === 'sv' ? '/sv/privacy' : '/privacy'}
          className="text-sm text-signal underline underline-offset-4 transition-colors hover:text-signal/80"
        >
          {t.privacy}
        </a>
      </div>
      {error && (
        <p className="mt-3 rounded-lg bg-bad/10 p-3 text-sm text-bad" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// Stored category codes (backend feedback schema); labels are translated
// (console.feedback.categories).
const FEEDBACK_CATEGORIES = ['bug', 'confusing', 'missing', 'idea', 'love_it'] as const;

function FeedbackView() {
  // The beta one-box (owner decision 2026-09-01): chips + text, no
  // fields. Rows are account-linked server-side — the note under the
  // box says so; that link is what makes "scores are wrong" actionable.
  const t = useT().console.feedback;
  const [category, setCategory] = useState('bug');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setSent(false);
    try {
      await api.post('/api/v1/feedback', { category, message });
      track('feedback_submitted', { category });
      setMessage('');
      setSent(true);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-6">
      <ViewHeader title={t.title} sub={t.sub} />

      <div className="rounded-xl border border-line bg-surface/80 p-5">
        <div className="flex flex-wrap gap-2">
          {FEEDBACK_CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              className={
                category === c
                  ? 'rounded-full border border-signal/50 bg-signal/10 px-3.5 py-1.5 text-sm font-medium text-hi transition'
                  : 'rounded-full border border-line px-3.5 py-1.5 text-sm text-mid transition hover:border-line-2 hover:text-hi'
              }
            >
              {t.categories[c]}
            </button>
          ))}
        </div>

        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          maxLength={1000}
          rows={6}
          placeholder={t.placeholder}
          className="mt-4 w-full resize-y rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
        />
        <div className="mt-2 flex items-center justify-between gap-4 text-xs text-low">
          <span>{t.linked}</span>
          <span className="num shrink-0">{message.length}/1000</span>
        </div>

        {error && (
          <p className="mt-3 rounded-lg bg-bad/10 p-3 text-sm text-hi" role="alert">
            {error}
          </p>
        )}
        {sent && !error && (
          <p className="mt-3 rounded-lg bg-ok/10 p-3 text-sm text-hi">
            {t.thanks}
          </p>
        )}

        <button
          onClick={submit}
          disabled={busy || !message.trim()}
          className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
        >
          {busy ? t.sending : t.send}
        </button>
      </div>
    </section>
  );
}

// The console language (2026-10-12). Lives in Settings so it is
// reachable on mobile (no sidebar there); the sidebar footer has the same
// toggle on desktop. Persisted per device (jfos-lang) — the same key the
// landing/login toggles write, so the choice follows the user across
// the public pages too.
function LanguageCard() {
  const t = useT().console.settings;
  const { locale, setLocale } = useLocale();
  const options: { id: Locale; label: string }[] = [
    { id: 'en', label: 'English' },
    { id: 'sv', label: 'Svenska' },
  ];
  return (
    <div className="rounded-xl border border-line bg-surface/80 p-5">
      <h3 className="font-semibold text-hi">{t.languageTitle}</h3>
      <p className="mt-1 max-w-md text-sm text-low">{t.languageBody}</p>
      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label={t.languageTitle}>
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            lang={o.id}
            aria-pressed={locale === o.id}
            onClick={() => setLocale(o.id)}
            className={
              locale === o.id
                ? 'rounded-full border border-signal/50 bg-signal/10 px-3.5 py-1.5 text-sm font-medium text-hi transition'
                : 'rounded-full border border-line px-3.5 py-1.5 text-sm text-mid transition hover:border-line-2 hover:text-hi'
            }
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function SettingsView() {
  const t = useT().console.settings;
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
      <ViewHeader title={t.title} sub={t.sub} />

      <LanguageCard />

      <YourDataCard />

      <div className="rounded-xl border border-line bg-surface/80 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-semibold text-hi">{t.gmailTitle}</h3>
            <p className="mt-1 max-w-md text-sm text-low">{t.gmailBody}</p>
          </div>
          {composio?.configured ? (
            <button
              onClick={connect}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              {busy ? t.opening : t.connect}
            </button>
          ) : (
            <span className="rounded-full bg-surface-2 px-2.5 py-1 text-xs text-low">
              {t.notConfigured}
            </span>
          )}
        </div>

        {composio && !composio.configured && (
          <p className="mt-4 rounded-lg bg-bad/10 p-3 text-sm text-hi" role="alert">
            {t.notAvailable}
          </p>
        )}

        {connected.length > 0 && (
          <div className="mt-4 space-y-1.5">
            {connected.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="capitalize text-mid">{a.app_name}</span>
                <span className="inline-flex items-center gap-1.5 text-ok">
                  <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden /> {t.connected}
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

// FE-23: the API is unreachable — say THAT, and offer the one action that
// can help (retrying the load). Same visual language as Warning (bad
// tokens + AlertTriangle) with a Retry that re-runs the full initial
// refresh, including the honest loading state.
function LoadFailure({ onRetry }: { onRetry: () => void }) {
  const t = useT().console.shared;
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-bad/30 bg-bad/10 p-4"
      role="alert"
    >
      <div className="flex items-start gap-2 text-sm text-hi">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-bad" />
        <div>
          {t.loadFailed}
          <span className="mt-0.5 block text-xs text-low">{t.loadFailedHint}</span>
        </div>
      </div>
      <button
        onClick={onRetry}
        className="shrink-0 rounded-lg bg-signal px-3 py-1.5 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98]"
      >
        {t.retry}
      </button>
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
