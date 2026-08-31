// Typed API client for JobFinderOS — TalentHive api.ts pattern with a
// long-running instance for the scrape+match pipeline.

import axios from 'axios';
import type { AxiosError, InternalAxiosRequestConfig } from 'axios';
import type {
  Application,
  ApplicationDraft,
  GeoData,
  IntegrationsStatus,
  Job,
  Match,
  OnboardingPayload,
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

// --- Auth token layer (Phase 1b) ---
// JWT lives in localStorage; every request carries it; any 401 clears it
// and sends the user to the login page.
export function getAuthToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('jfos-token');
}

export function setAuthToken(token: string | null): void {
  if (token === null) localStorage.removeItem('jfos-token');
  else localStorage.setItem('jfos-token', token);
}

export function logout(): void {
  setAuthToken(null);
  if (typeof window !== 'undefined') window.location.href = '/login';
}

// OPS-1: the API lives on a Render free-tier web service that spins down
// after idle; the first request after spin-down eats a ~1min cold start
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

// Attach the JWT to every request from BOTH instances, and handle expiry:
// a 401 while a token is in storage means the session expired (or was
// revoked) — clear it and go to /login. The pathname guard keeps a failed
// login POST (its own 400/401) from reloading /login and swallowing the
// inline error message. Without these interceptors the token layer was
// dead code: login stored a JWT that no request ever attached.
for (const instance of [api, slowApi]) {
  instance.interceptors.request.use((config) => {
    const token = getAuthToken();
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
        getAuthToken() &&
        window.location.pathname !== '/login'
      ) {
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

export const suggestQueries = async (
  country: string,
  mode: 'field' | 'adjacent' | 'widen' = 'field'
): Promise<QuerySuggestions> => {
  const response = await slowApi.post<QuerySuggestions>(
    `/api/v1/profile/suggest-queries?country=${country}&mode=${mode}`
  );
  return response.data;
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

export const runMatching = async (limit?: number): Promise<{ status: string }> => {
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
  redirectUri = `${window.location.origin}/`
): Promise<{ redirect_url: string }> => {
  const response = await api.post<{ redirect_url: string }>(
    '/api/v1/settings/integrations/composio/connect',
    { app_name: appName, redirect_uri: redirectUri }
  );
  return response.data;
};

// ---------- Applications ----------

// FE-12: pure planner for the job-join page-walk in getApplications —
// exported (like shouldRetryGet above) so the batching logic can be
// sanity-checked out-of-band; this repo has no JS test harness.
//
// The walk stops when:
//   - every referenced job id is resolved (typical: newest page suffices
//     → still exactly ONE request), or
//   - the last page came back short (end of the job list — remaining ids
//     reference deleted jobs and will never resolve), or
//   - the next offset would pass maxOffset, a documented ceiling that
//     also bounds a misbehaving server that ignores `offset` and replays
//     the same full page forever.
export type JoinWalkPlan = { continueWalking: boolean; nextOffset: number };

export function nextJoinWalkPage(input: {
  neededIds: ReadonlySet<number>;
  foundIds: ReadonlySet<number>;
  lastPageSize: number;
  requestedLimit: number;
  offset: number;
  maxOffset: number;
}): JoinWalkPlan {
  for (const id of input.neededIds) {
    if (!input.foundIds.has(id)) {
      // Still missing at least one id — continue unless the list ended or
      // the walk ceiling was reached.
      const next = input.offset + input.lastPageSize;
      if (input.lastPageSize < input.requestedLimit || next > input.maxOffset) {
        return { continueWalking: false, nextOffset: input.offset };
      }
      return { continueWalking: true, nextOffset: next };
    }
  }
  return { continueWalking: false, nextOffset: input.offset };
}

// The applications response carries only job_id (ApplicationResponse has
// no job summary), so titles/companies are joined client-side. The jobs
// endpoint (backend/app/api/v1/jobs.py) offers NO id filter and caps
// `limit` at 500 — the old single `?limit=500` fetch silently missed
// every job past the first 500, degrading the sent list to "Job #id"
// once the pool grew. Below, pages of 500 are walked via `offset` until
// every referenced id resolves (or the list ends), so the join is
// complete regardless of pool size — no backend change needed.
const JOB_JOIN_PAGE_SIZE = 500; // must match the endpoint's `le=500` cap
const JOB_JOIN_MAX_WALKED = 10_000; // 20 pages — ceiling, see nextJoinWalkPage

export const getApplications = async (): Promise<(Application & { job?: Job })[]> => {
  const response = await api.get<Application[]>('/api/v1/applications/');
  const applications = response.data;
  if (applications.length === 0) return applications;

  const neededIds = new Set(applications.map((a) => a.job_id));
  const jobMap = new Map<number, Job>();
  for (let offset = 0; ; ) {
    const page = await api.get<Job[]>('/api/v1/jobs/', {
      params: { limit: JOB_JOIN_PAGE_SIZE, offset },
    });
    for (const j of page.data) jobMap.set(j.id, j);
    const foundIds = new Set([...neededIds].filter((id) => jobMap.has(id)));
    const plan = nextJoinWalkPage({
      neededIds,
      foundIds,
      lastPageSize: page.data.length,
      requestedLimit: JOB_JOIN_PAGE_SIZE,
      offset,
      maxOffset: JOB_JOIN_MAX_WALKED,
    });
    if (!plan.continueWalking) break;
    offset = plan.nextOffset;
  }
  return applications.map((a) => ({ ...a, job: jobMap.get(a.job_id) }));
};

export const retryApplication = async (applicationId: number): Promise<Application> => {
  const response = await api.post<Application>(`/api/v1/applications/${applicationId}/retry`);
  return response.data;
};
