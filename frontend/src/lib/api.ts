// Typed API client for JobFinderOS — TalentHive api.ts pattern with a
// long-running instance for the scrape+match pipeline.

import axios from 'axios';
import { consoleLocale } from '@/i18n/locale';
import type { AxiosError, InternalAxiosRequestConfig } from 'axios';
import type {
  AiJob,
  Application,
  ApplicationDraft,
  GeoData,
  IntegrationsStatus,
  Match,
  OnboardingPayload,
  OutcomeNudges,
  OutcomeProposal,
  OutcomeStats,
  OutcomeStatus,
  PipelineRunResponse,
  PipelineStatusResponse,
  Profile,
  ProfileStatus,
  QuerySuggestions,
} from '@/types';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Surface the backend's carefully written error `detail` (axios docs:
// response interceptors) — err.message alone is the useless
// "Request failed with status code 400" string.
function apiErrorMessage(error: unknown): string {
  const axios = error as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = axios?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (detail) return JSON.stringify(detail);
  return axios?.message ?? 'Request failed';
}

export { apiErrorMessage };

// --- Auth session layer (MIG-WO2) ---
// Supabase Auth owns the session (short-lived access token + refresh,
// auto-refreshed by the SDK); every request carries the CURRENT access
// token; any 401 signs the session out and sends the user to login.
// The old jfos-token localStorage JWT is dead — getSessionToken is the
// only token source.
import { getSessionToken, supabase } from '@/lib/supabase';

export { getSessionToken };

export async function logout(): Promise<void> {
  // scope: 'local' — only THIS browser's session. The SDK's default is
  // 'global', which kills the user's session on every device: sidebar
  // Sign out must not log their phone out, and a single transient 401
  // (e.g. a backend-side JWKS blip) must not sign them out everywhere
  // (review finding 2026-09-08).
  await supabase.auth.signOut({ scope: 'local' });
  // Land on the login page in the user's language (sign-out, expired
  // session, any 401) — no English hop before LoginView's sv redirect.
  if (typeof window !== 'undefined') {
    window.location.href = consoleLocale() === 'sv' ? '/sv/login' : '/login';
  }
}

// OPS-1: the API lived on a Render Free instance that spins down after
// idle (paid 0.5c-512mb since 2026-10-15 — no more spin-down, but a
// deploy/restart still makes the API briefly unreachable, so the long
// timeout + one retry stay). On Free, the first request after spin-down ate
// a ~1min cold start
// (the repo's own ops verify script budgets 90s per attempt, 6 attempts).
// A 60s timeout failed BEFORE the service finished waking, so every cold
// visit looked like "API down". 120s lets the first GET outlast the cold
// start instead of racing it. The slowApi pipeline timeout is untouched.
export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
});

// Pipeline runs scrape + AI matching — needs a long timeout (TalentHive pattern)
const slowApi = axios.create({
  baseURL: API_BASE_URL,
  timeout: 600000,
  headers: { 'Content-Type': 'application/json' },
});

// ---------- OPS-1: retry-on-network-failure for GETs only ----------

// Pure decision function for the GET retry below — exported so the logic
// can be sanity-checked out-of-band (this repo has no JS test harness,
// per PR #7). Kept free of axios types on purpose: callers boil the error
// down to {method, hasResponse, code, attempt}.
export type RetryDecisionInput = {
  /** HTTP method of the failed request; '' treated as GET (axios default) */
  method?: string;
  /** true when the server actually answered (any status) — not a transport failure */
  hasResponse: boolean;
  /** axios error code — 'ECONNABORTED' is a timeout, 'ERR_NETWORK' a dropped/refused connection */
  code?: string;
  /** retries already spent on this request (0 on the original attempt) */
  attempt: number;
};

// Transport failures worth one retry. A cold start can manifest as either
// a timeout (request accepted, service still booting) or a reset/refused
// connection (proxy cycling while the container starts).
const RETRYABLE_CODES = new Set([
  'ECONNABORTED', // axios timeout
  'ETIMEDOUT',
  'ERR_NETWORK', // refused/reset/unreachable — umbrella axios code
  'ECONNREFUSED',
  'ECONNRESET',
  'ERR_EMPTY_RESPONSE',
]);

// Retry a failed request at most ONCE, and ONLY idempotent GETs that never
// reached the server:
//   - mutations (POST/PUT) NEVER retry: a retried submit/applications call
//     could double-send an email application, and the backend has no
//     idempotency keys; even a "safe-looking" PUT (draft save) could clobber
//     a newer server-side write (AI re-tailoring) with stale text.
//   - a request that got ANY response (4xx/5xx) never retries — the server
//     answered; retrying turns a fast, honest error into a slow one.
//   - non-transport failures (bad JSON, cancelled) have no retryable code.
export function shouldRetryGet(info: RetryDecisionInput): boolean {
  if (info.attempt >= 1) return false; // one retry, never two
  const method = (info.method || 'get').toLowerCase();
  if (method !== 'get') return false;
  if (info.hasResponse) return false;
  return RETRYABLE_CODES.has(info.code ?? '');
}

// Config carries its own retry count so the interceptor stays stateless.
type RetryableConfig = InternalAxiosRequestConfig & { __jfosRetryCount?: number };
const GET_RETRY_DELAY_MS = 500;

function retryDecision(error: AxiosError, config: RetryableConfig | undefined) {
  return shouldRetryGet({
    method: config?.method,
    hasResponse: error.response !== undefined,
    code: error.code,
    attempt: config?.__jfosRetryCount ?? 0,
  });
}

// Attach the session token to every request from BOTH instances, and
// handle expiry: a 401 while a session exists means the token was
// rejected (expired beyond refresh, or the account died) — sign out and
// go to /login. The pathname guard keeps a failed login POST (its own
// 400/401) from reloading /login and swallowing the inline error
// message. The request interceptor is async: the access token comes
// from the Supabase session (SDK may refresh it first).
for (const instance of [api, slowApi]) {
  instance.interceptors.request.use(async (config) => {
    const token = await getSessionToken();
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  });
  instance.interceptors.response.use(
    (r) => r,
    async (error: AxiosError) => {
      // OPS-1 GET retry — runs BEFORE the 401 handling below because a
      // retried request deserves the same expiry treatment on its second
      // attempt (a transport failure has no response, so the 401 branch
      // can't fire for it anyway).
      const config = error.config as RetryableConfig | undefined;
      if (config && retryDecision(error, config)) {
        config.__jfosRetryCount = (config.__jfosRetryCount ?? 0) + 1;
        await new Promise((resolve) => setTimeout(resolve, GET_RETRY_DELAY_MS));
        return instance.request(config);
      }
      if (
        error?.response?.status === 401 &&
        window.location.pathname !== '/login' &&
        window.location.pathname !== '/sv/login' &&
        window.location.pathname !== '/reset-password' &&
        window.location.pathname !== '/sv/reset-password'
      ) {
        // NOT conditioned on a live session (review round 2): the most
        // common 401 shape is an already-dead session (expired/revoked
        // refresh token) — getSessionToken() returning null must still
        // leave the login page, or the console mounts forever with
        // every poller firing 401s and no way back in.
        logout();
      }
      return Promise.reject(error);
    },
  );
}

// ---------- Profile ----------

export const uploadCv = async (file: File): Promise<Profile> => {
  const formData = new FormData();
  formData.append('file', file);
  // WO-31: returns once the CV is saved — the AI reading of it is a
  // queued job (profile.extraction) that fills the fields in later.
  const response = await slowApi.post<Profile>('/api/v1/profile/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

export const getProfile = async (): Promise<Profile | null> => {
  try {
    const response = await api.get<Profile>('/api/v1/profile/me');
    return response.data;
  } catch (err: unknown) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return null;
    throw err;
  }
};

export const updateProfile = async (prefs: Partial<{
  full_name: string;
  email: string;
  phone: string;
  location: string;
  preferred_roles: string[];
  preferred_locations: string;
  remote_ok: boolean;
  min_salary: string;
  exclude_keywords: string[];
  vouched_facts: string[];
  work_rights?: string;
}>): Promise<Profile> => {
  const response = await api.put<Profile>('/api/v1/profile/me', prefs);
  return response.data;
};

export const getProfileStatus = async (): Promise<ProfileStatus> => {
  const response = await api.get<ProfileStatus>('/api/v1/profile/status');
  return response.data;
};

// ---------- Onboarding ----------

export const getGeo = async (): Promise<GeoData> => {
  const response = await api.get<GeoData>('/api/v1/profile/geo');
  return response.data;
};

// WO-31: status of one queued AI job (the poll behind every "you're in
// a queue" message).
export const getAiJob = async (jobId: number): Promise<AiJob> => {
  const response = await api.get<AiJob>(`/api/v1/ai-jobs/${jobId}`);
  return response.data;
};

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Poll a queued job until it finishes. onProgress sees every queued /
// running state so the UI can show honest copy. Provider load never
// ends this — only done or a non-load failure does.
export const waitForAiJob = async (
  job: AiJob,
  onProgress?: (job: AiJob) => void,
  intervalMs = 2000,
  // A component that unmounts stops its poll; the job itself keeps
  // running server-side and its result still lands.
  isCancelled?: () => boolean
): Promise<AiJob> => {
  let current = job;
  let consecutiveFailures = 0;
  while (current.status === 'queued' || current.status === 'running') {
    if (isCancelled?.()) return current;
    onProgress?.(current);
    await sleep(intervalMs);
    if (isCancelled?.()) return current;
    try {
      current = await getAiJob(current.id);
      consecutiveFailures = 0;
    } catch {
      // transient (cold start, network): keep waiting on the same job.
      // But a poll that NEVER succeeds (job pruned, persistent 5xx)
      // must not loop forever — the eternal spinner hid the manual
      // fallback and locked the wizard (2026-10-30 audit). After 5
      // straight failures the wait throws; callers surface it.
      if (++consecutiveFailures >= 5) {
        throw new Error('job status unreachable — try again');
      }
    }
  }
  return current;
};

export const suggestQueries = async (
  country: string,
  mode: 'field' | 'adjacent' | 'widen' = 'field',
  onProgress?: (job: AiJob) => void
): Promise<QuerySuggestions> => {
  const response = await api.post<QuerySuggestions & { job?: AiJob }>(
    `/api/v1/profile/suggest-queries?country=${country}&mode=${mode}`
  );
  const { job } = response.data;
  if (!job || job.status === 'done') return response.data;
  const finished = await waitForAiJob(job, onProgress);
  if (finished.status !== 'done' || !finished.result) {
    throw new Error(finished.error ?? 'suggestion failed');
  }
  return finished.result as QuerySuggestions;
};

export const saveOnboarding = async (payload: OnboardingPayload): Promise<Profile> => {
  const response = await api.post<Profile>('/api/v1/profile/onboarding', payload);
  return response.data;
};

// ---------- Pipeline ----------

export const runPipeline = async (options?: {
  sources?: string[];
  match?: boolean;
  max_matches?: number;
  backfill?: boolean;
}): Promise<PipelineRunResponse> => {
  const response = await slowApi.post<PipelineRunResponse>('/api/v1/pipeline/run', {
    sources: options?.sources,
    match: options?.match ?? true,
    max_matches: options?.max_matches,
    backfill: options?.backfill ?? false,
  });
  return response.data;
};

export const runMatching = async (limit?: number): Promise<{ status: string; message?: string }> => {
  const response = await api.post(`/api/v1/matches/run${limit ? `?limit=${limit}` : ''}`);
  return response.data;
};

export const getPipelineStatus = async (): Promise<PipelineStatusResponse> => {
  const response = await api.get<PipelineStatusResponse>('/api/v1/pipeline/status');
  return response.data;
};

// ---------- Matches ----------

export const getMatches = async (params?: {
  tier?: string;
  pending_only?: boolean;
  min_score?: number;
  limit?: number;
}): Promise<Match[]> => {
  const response = await api.get<Match[]>('/api/v1/matches/', { params });
  return response.data;
};

export const decideMatch = async (
  matchId: number,
  decision: 'approved' | 'rejected'
): Promise<Match> => {
  const response = await api.post<Match>(`/api/v1/matches/${matchId}/decision`, { decision });
  return response.data;
};

// ---------- Drafts (application preparation stage) ----------

export const prepareDraft = async (jobId: number, force = false): Promise<ApplicationDraft> => {
  const response = await slowApi.post<ApplicationDraft>(
    `/api/v1/applications/draft/${jobId}${force ? '?force=true' : ''}`
  );
  return response.data;
};

export const getDrafts = async (): Promise<ApplicationDraft[]> => {
  const response = await api.get<ApplicationDraft[]>('/api/v1/applications/drafts');
  return response.data;
};

export const updateDraft = async (
  draftId: number,
  edits: { cover_letter?: string; tailored_cv?: string }
): Promise<ApplicationDraft> => {
  const response = await api.put<ApplicationDraft>(
    `/api/v1/applications/draft/${draftId}`,
    edits
  );
  return response.data;
};

// WO-23 blocked-draft recovery: run the fabrication guard over the
// draft's CURRENT text (clean -> ready), and per-claim "This is true —
// keep it". Confirming the LAST claim re-runs the guard on the current
// text before ready (review fix R1) — it can take a few seconds.
export const recheckDraft = async (draftId: number): Promise<ApplicationDraft> => {
  const response = await slowApi.post<ApplicationDraft>(
    `/api/v1/applications/draft/${draftId}/recheck`
  );
  return response.data;
};

export const attestDraft = async (
  draftId: number,
  claim: string,
  saveToProfile = false,
  // Round 4: saveToProfile defaults FALSE — our UI never sets it true
  // (per-claim profile saving had no safe form; permanent vouching goes
  // through the profile editor). The backend additionally only saves a
  // fact that exactly equals the confirmed claim.
  profileFact?: string,
  // Round 5: the finding's sentence — pins WHICH flagged use of a
  // repeated value the user confirmed. The value's other uses stay
  // flagged.
  context?: string
): Promise<ApplicationDraft> => {
  const response = await slowApi.post<ApplicationDraft>(
    `/api/v1/applications/draft/${draftId}/attest`,
    { claim, save_to_profile: saveToProfile, profile_fact: profileFact, context }
  );
  return response.data;
};

export const submitDraft = async (
  draftId: number,
  method: 'email' | 'browser' | 'manual'
): Promise<Application> => {
  const response = await api.post<Application>(
    `/api/v1/applications/draft/${draftId}/submit`,
    { method }
  );
  return response.data;
};

// PDF downloads — fetched through the axios instance (Bearer token attached)
// as blobs, then handed to the browser via a programmatic <a download>
// click. Two bugs fixed vs the old window.open(objectURL):
//   1. Safari: window.open() called AFTER an await loses the click's user
//      activation (consumed by the network round-trip), so Safari's popup
//      blocker silently ate it — and the "Popup blocked" throw was
//      unreachable dead code. An anchor click on a blob URL needs no user
//      activation and no popup permission. Callers catch rejections and
//      surface them through the console's error banner (FE-22).
//   2. The anchor carries `download`, so the file saves with a real name
//      (Content-Disposition when the server sends one, else the fallback).
async function downloadPdfBlob(path: string, fallbackFilename: string): Promise<void> {
  const response = await api.get<Blob>(path, { responseType: 'blob' });
  const disposition = response.headers?.['content-disposition'];
  const match =
    typeof disposition === 'string' ? /filename="?([^";]+)"?/i.exec(disposition) : null;
  const filename = match?.[1] ?? fallbackFilename;
  const url = URL.createObjectURL(response.data);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Revoke after the browser has surely grabbed the blob (best-effort
    // cleanup — too early and Safari cancels the in-flight download).
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
}

export const downloadDraftCoverLetterPdf = (draftId: number) =>
  downloadPdfBlob(
    `/api/v1/applications/draft/${draftId}/download/cover-letter`,
    'jobfinderos-cover-letter.pdf'
  );
export const downloadDraftCvPdf = (draftId: number) =>
  downloadPdfBlob(
    `/api/v1/applications/draft/${draftId}/download/cv`,
    'jobfinderos-cv.pdf'
  );

// ---------- Settings / integrations ----------

// OPS-6: GDPR rights, wired to the backend's account endpoints
// (backend/app/api/v1/account.py). The privacy notice and the
// point-of-collection panels promise "Settings → Your data" — these two
// calls are what make that claim true.

// Right to portability: GET /account/export returns the account, profile,
// matches and applications as JSON. Fetched as JSON (not a blob) so an
// error keeps its `detail` message; the file is assembled client-side.
export const exportAccountData = async (): Promise<void> => {
  const response = await api.get<Record<string, unknown>>('/api/v1/account/export');
  const blob = new Blob([JSON.stringify(response.data, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'jobfinderos-export.json';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
};

// Right to erasure: DELETE /account/delete removes the profile (+ the CV
// file from storage), matches, drafts, applications and the account
// itself. Callers must log the user out and route them away afterwards —
// the token they hold is dead the moment this succeeds.
export const deleteAccount = async (): Promise<void> => {
  await api.delete('/api/v1/account/delete');
};

export const getIntegrations = async (): Promise<IntegrationsStatus> => {
  const response = await api.get<IntegrationsStatus>('/api/v1/settings/integrations');
  return response.data;
};

export const connectComposio = async (
  appName: string,
  redirectUri = `${window.location.origin}/app`
): Promise<{ redirect_url: string }> => {
  const response = await api.post<{ redirect_url: string }>(
    '/api/v1/settings/integrations/composio/connect',
    { app_name: appName, redirect_uri: redirectUri }
  );
  return response.data;
};

// ---------- Applications ----------

// EGRESS 2026-09-11: the backend embeds each application's job in the
// response (ApplicationResponse.job — the same JobResponse shape
// /jobs/ serves). The old client-side join WALKED the whole /jobs/
// pool 500 rows at a time per fetch to resolve titles (~900 full
// rows per refresh; 2,081 walks in two weeks of pg_stat_statistics —
// the #2 driver of Supabase egress hitting 109% of the free tier).
// The walk, its planner, and the /jobs/ dependency are gone.
export const getApplications = async (): Promise<Application[]> => {
  const response = await api.get<Application[]>('/api/v1/applications/');
  return response.data;
};

export const retryApplication = async (applicationId: number): Promise<Application> => {
  const response = await api.post<Application>(`/api/v1/applications/${applicationId}/retry`);
  return response.data;
};

// --- WO-20 outcome layer ---------------------------------------------
// What the employer did — manual markers, propose-then-confirm reply
// detection, nudges, and the honest-stats surface.

export const markOutcome = async (
  applicationId: number,
  outcomeStatus: OutcomeStatus,
  note?: string,
): Promise<Application> => {
  const response = await api.post<Application>(
    `/api/v1/outcomes/${applicationId}/outcome`,
    { outcome_status: outcomeStatus, note: note ?? null },
  );
  return response.data;
};

export const createFollowUp = async (applicationId: number): Promise<ApplicationDraft> => {
  const response = await api.post<ApplicationDraft>(
    `/api/v1/outcomes/${applicationId}/follow-up`,
  );
  return response.data;
};

export const getOutcomeProposals = async (): Promise<OutcomeProposal[]> => {
  const response = await api.get<OutcomeProposal[]>('/api/v1/outcomes/proposals');
  return response.data;
};

export const decideOutcomeProposal = async (
  proposalId: number,
  decision: 'approved' | 'dismissed',
): Promise<Application> => {
  const response = await api.post<Application>(
    `/api/v1/outcomes/proposals/${proposalId}/decide`,
    { decision },
  );
  return response.data;
};

export const approveAllOutcomeProposals = async (): Promise<{ approved: number }> => {
  const response = await api.post<{ approved: number }>('/api/v1/outcomes/proposals/approve-all');
  return response.data;
};

export const getOutcomeNudges = async (): Promise<OutcomeNudges> => {
  const response = await api.get<OutcomeNudges>('/api/v1/outcomes/nudges');
  return response.data;
};

export const getOutcomeStats = async (): Promise<OutcomeStats> => {
  const response = await api.get<OutcomeStats>('/api/v1/outcomes/stats');
  return response.data;
};
