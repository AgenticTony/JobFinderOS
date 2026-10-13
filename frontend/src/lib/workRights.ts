// WO-19 part B work-rights codes (mirror backend WORK_RIGHTS_VALUES).
// Labels: i18n console.workRights. Pure module — node --test loads it.

// The wizard's short list (the Profile select offers all five).
export const WIZARD_WORK_RIGHTS = ['citizen_or_pr', 'eu_right', 'needs_sponsorship', 'prefer_not_say'] as const;

// The Profile select's full list, in display order. Single home beside
// WIZARD_WORK_RIGHTS so both mirrors of the backend list are testable
// together — the wizard's saved answer can be any of these five.
export const PROFILE_WORK_RIGHTS = [
  'citizen_or_pr',
  'permanent_resident',
  'eu_right',
  'needs_sponsorship',
  'prefer_not_say',
] as const;

// Options the wizard shows when re-run for a user whose saved answer may
// be outside the short list ('permanent_resident' is set from Profile):
// the saved answer is always offered — and, being the prefill, selected —
// so re-running setup can never silently change it (bug 2026-10-13: the
// wizard started at prefer_not_say and saved that over the real answer).
export function wizardWorkRightsOptions(saved: string | null | undefined): string[] {
  const base: string[] = [...WIZARD_WORK_RIGHTS];
  if (saved && !base.includes(saved)) base.splice(base.length - 1, 0, saved);
  return base;
}

// The wizard's starting value: the saved answer, else prefer_not_say.
export function initialWizardWorkRights(saved: string | null | undefined): string {
  return saved || 'prefer_not_say';
}
