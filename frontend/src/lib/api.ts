// Typed API client for JobFinderOS — TalentHive api.ts pattern with a
// long-running instance for the scrape+match pipeline.

import axios from 'axios';
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

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

// Pipeline runs scrape + AI matching — needs a long timeout (TalentHive pattern)
const slowApi = axios.create({
  baseURL: API_BASE_URL,
  timeout: 600000,
  headers: { 'Content-Type': 'application/json' },
});

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
    (error) => {
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
}): Promise<PipelineRunResponse> => {
  const response = await slowApi.post<PipelineRunResponse>('/api/v1/pipeline/run', {
    sources: options?.sources,
    match: options?.match ?? true,
    max_matches: options?.max_matches,
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
// as blobs, then opened via object URLs. The old window.open() was a plain
// navigation that carried no Authorization header and 401'd every time.
async function downloadPdfBlob(path: string): Promise<void> {
  const response = await api.get(path, { responseType: 'blob' });
  const url = URL.createObjectURL(response.data);
  const window2 = window.open(url, '_blank', 'noopener');
  // Revoke after the browser has the blob (best-effort cleanup)
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  if (!window2) throw new Error('Popup blocked — allow popups to download PDFs');
}

export const downloadDraftCoverLetterPdf = (draftId: number) =>
  downloadPdfBlob(`/api/v1/applications/draft/${draftId}/download/cover-letter`);
export const downloadDraftCvPdf = (draftId: number) =>
  downloadPdfBlob(`/api/v1/applications/draft/${draftId}/download/cv`);

// ---------- Settings / integrations ----------

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

export const getApplications = async (): Promise<(Application & { job?: Job })[]> => {
  const response = await api.get<Application[]>('/api/v1/applications/');
  const applications = response.data;
  // Join job info client-side for display
  if (applications.length > 0) {
    const jobs = await api.get<Job[]>('/api/v1/jobs/', { params: { limit: 500 } });
    const jobMap = new Map(jobs.data.map((j) => [j.id, j]));
    return applications.map((a) => ({ ...a, job: jobMap.get(a.job_id) }));
  }
  return applications;
};

export const retryApplication = async (applicationId: number): Promise<Application> => {
  const response = await api.post<Application>(`/api/v1/applications/${applicationId}/retry`);
  return response.data;
};
