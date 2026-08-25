// Typed API client for JobFinderOS — TalentHive api.ts pattern with a
// long-running instance for the scrape+match pipeline.

import axios from 'axios';
import type {
  Application,
  ApplicationDraft,
  GeoData,
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

// Direct PDF download URLs (browser handles the download via Content-Disposition)
export const draftCoverLetterPdfUrl = (draftId: number) =>
  `${API_BASE_URL}/api/v1/applications/draft/${draftId}/download/cover-letter`;
export const draftCvPdfUrl = (draftId: number) =>
  `${API_BASE_URL}/api/v1/applications/draft/${draftId}/download/cv`;

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
