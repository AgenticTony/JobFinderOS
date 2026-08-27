// Shared types for JobFinderOS frontend

export type Tier = 'excellent_match' | 'good_match' | 'stretch' | 'poor_match';
export type Recommendation = 'apply' | 'maybe' | 'skip';
export type JobStatus = 'new' | 'matched' | 'approved' | 'rejected' | 'dismissed' | 'applied';
export type ApplicationMethod = 'email' | 'browser' | 'manual';
export type ApplicationStatus = 'queued' | 'sent' | 'failed' | 'manual_pending';

export interface Job {
  id: number;
  source: string;
  source_id: string | null;
  title: string;
  company: string | null;
  location: string | null;
  remote: boolean;
  url: string;
  employment_type: string | null;
  salary: string | null;
  status: JobStatus;
  application_email: string | null;
  application_url: string | null;
  published_at: string | null;
  scraped_at: string;
  tags: string[];
}

export interface Match {
  id: number;
  job_id: number;
  score: number;
  tier: Tier;
  reasoning: string | null;
  matched_skills: string[];
  missing_skills: string[];
  transferable_skills: string[];
  recommendation: Recommendation | null;
  confidence: string | null;
  decision: 'approved' | 'rejected' | null;
  decided_at: string | null;
  created_at: string;
  job: Job | null;
}

export interface Application {
  id: number;
  job_id: number;
  match_id: number | null;
  draft_id: number | null;
  method: ApplicationMethod;
  status: ApplicationStatus;
  subject: string | null;
  body: string | null;
  target_email: string | null;
  apply_url: string | null;
  sent_at: string | null;
  error: string | null;
  created_at: string;
}

export interface ApplicationDraft {
  id: number;
  job_id: number;
  match_id: number | null;
  cover_letter: string | null;
  tailored_cv: string | null;
  changes_summary: string[];
  status: 'drafting' | 'ready' | 'submitted' | 'failed';
  error: string | null;
  // WO-01 fabrication guard: advisory (technology-class) findings for the
  // review UI; high-confidence ones drove regeneration or a block instead
  fabrication_findings: { kind: string; value: string; context: string; tier: string }[];
  fabrication_retries: number;
  fabrication_blocked: boolean;
  created_at: string;
  updated_at: string;
  job: Job | null;
}

export interface Profile {
  id: number;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  location: string | null;
  professional_title: string | null;
  cv_file_name: string | null;
  experience_years: number | null;
  ai_summary: string | null;
  preferred_roles: string[];
  preferred_locations: string | null;
  remote_ok: boolean;
  min_salary: string | null;
  exclude_keywords: string[];
  onboarded: boolean;
  country: string | null;
  region: string | null;
  municipality: string | null;
  remote_only: boolean;
  include_remote: boolean;
  search_queries: string[];
  languages: string[];
  skills: { name: string; level?: string }[];
  recent_roles: { title?: string; company?: string; period?: string; highlights?: string }[];
  education: { degree?: string; field?: string; institution?: string; year?: string }[];
  certifications: string[];
  keywords: string[];
  created_at: string;
  updated_at: string;
}

export interface OnboardingPayload {
  country: string;
  region?: string | null;
  municipality?: string | null;
  remote_only: boolean;
  include_remote: boolean;
  search_queries: string[];
  languages: string[];
}

export interface GeoData {
  countries: { code: string; name: string; flag: string }[];
  geo: Record<string, Record<string, string[]>>;
}

export type SearchMode = 'field' | 'adjacent' | 'widen';

export interface QuerySuggestions {
  country: string;
  mode: SearchMode;
  from_your_experience: string[];
  worth_a_look: { query: string; why: string }[];
}

export interface Stats {
  jobs_total: number;
  jobs_last_24h: number;
  jobs_new: number;
  jobs_matched: number;
  jobs_approved: number;
  jobs_rejected: number;
  jobs_dismissed: number;
  jobs_applied: number;
  matches_total: number;
  matches_excellent: number;
  matches_good: number;
  matches_pending_decision: number;
  applications_total: number;
  applications_sent: number;
  applications_manual_pending: number;
  applications_failed: number;
}

export interface ProfileStatus {
  has_profile: boolean;
  has_cv_text: boolean;
  ai_enabled: boolean;
  email_apply_enabled: boolean;
  stats: Stats;
}

export interface ScrapeSummary {
  source: string;
  status: string;
  jobs_found: number;
  jobs_new: number;
  error: string | null;
}

export interface MatchSummary {
  status: string;
  jobs_considered: number;
  matches_created: number;
  skipped_no_profile?: boolean;
  error?: string | null;
}

export interface PipelineRunResponse {
  scrape: ScrapeSummary[];
  match: MatchSummary | null;
  top_matches: Match[];
}

export interface ComposioAccount {
  id: string;
  app_name: string;
  status: string;
  created_at: string | null;
}

export interface IntegrationsStatus {
  composio: {
    configured: boolean;
    accounts: ComposioAccount[];
  };
}

export interface PipelineStatusResponse {
  sources_available: string[];
  sources_enabled: string[];
  scheduler_enabled: boolean;
  scrape_interval_minutes: number;
  next_run_at: string | null;
  matching_running: boolean;
  stats: Stats;
  recent_runs: {
    source: string;
    status: string;
    jobs_found: number;
    jobs_new: number;
    error: string | null;
    started_at: string;
  }[];
}
