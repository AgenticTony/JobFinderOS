'use client';

// Onboarding wizard — first-time setup that personalizes the whole OS:
// CV (the source of truth every later step reads) -> country (source
// pack) -> region/city + remote (location filter) -> AI-suggested job
// titles from the CV (search queries) -> confirm. The CV is step 1 by
// design: users expect to hand it over on a job platform, and the AI
// titles step cannot work without it (the suggest route 400s on a
// CV-less profile — previously the wizard opened for CV-less users and
// dead-ended there with only manual entry).

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Compass,
  FileText,
  Globe2,
  Languages,
  Loader2,
  MapPin,
  Plus,
  Radar,
  Search,
  Target,
  X,
} from 'lucide-react';
import CvUpload from '@/components/CvUpload';
import { apiErrorMessage, getGeo, suggestQueries } from '@/lib/api';
import type { GeoData, OnboardingPayload, OccupationSuggestion, SearchMode } from '@/types';
import { cn } from '@/lib/utils';

interface Props {
  onComplete: (payload: OnboardingPayload) => Promise<void>;
  onClose?: () => void; // optional — wizard is modal but re-openable from Profile
  // Step 1's upload target — the parent owns the API call (and the
  // profile refresh after it), exactly like the Profile tab's upload.
  onUploadCv: (file: File) => Promise<void>;
  initialHasCv?: boolean; // edit-mode re-runs: CV already on file, step 1 passes
  initialLanguages?: string[]; // prefill when re-running setup
  initialIncludeRemote?: boolean; // prefill when re-running setup
  // Edit-mode prefill: existing setup so "Edit setup" never wipes the
  // user's curated search queries or forces re-picking country/region
  initialCountry?: string;
  initialRegion?: string;
  initialMunicipality?: string;
  initialMunicipalities?: string[];
  initialSearchRadiusKm?: number;
  // Saved taxonomy picks ({code,label}) — edit mode pre-renders them
  // as chips AND pre-selects them, so a stored profession is always
  // visible and deselectable, never an invisible submit payload
  // (review finding: codes-only prefill left saved professions active
  // but unrendered when the fresh suggestion call didn't return them).
  initialOccupations?: OccupationSuggestion[];
  initialQueries?: string[];
}

const STEPS = ['Your CV', 'Country', 'Location', 'Languages', 'Job titles', 'Confirm'] as const;

const LANGUAGE_OPTIONS = [
  'English',
  'Swedish',
  'German',
  'French',
  'Spanish',
  'Danish/Norwegian',
  'Finnish',
  'Italian',
  'Dutch',
];

const MODES: { id: SearchMode; label: string; hint: string; icon: typeof Target }[] = [
  { id: 'field', label: 'Stay in my field', hint: 'Job titles from my CV', icon: Target },
  { id: 'adjacent', label: 'Open to adjacent roles', hint: 'My field, plus near-neighbours', icon: Search },
  {
    id: 'widen',
    label: 'Widen my options',
    hint: 'My field is shrinking or I\u2019m changing direction — use my transferable skills',
    icon: Compass,
  },
];

// FE-10: minimal modal machinery for the wizard dialog — focus trap,
// initial focus, focus restore, and scroll lock. Inline (no dependency,
// no shared hook file yet: this is the app's only modal).
//
// Escape is DELIBERATELY ignored: closing the wizard discards the user's
// unsaved picks (country, area, curated titles), and first-run mode has no
// close affordance at all — an invisible Escape shortcut doing that would
// be destructive without confirmation. The explicit exit paths stay: the
// visible Close button (re-run mode) and Back navigation. A window.confirm
// guard was considered (the P0-4 switchView pattern) but rejected: it
// invents a confirm for a shortcut nobody asked for — the X is the
// deliberate, visible equivalent.
function useFocusTrap(active: boolean) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!active) return;
    const container = ref.current;
    if (!container) return;

    // Where focus came from — restored on close so keyboard users land
    // back on the invoking element ("Edit setup"), not the page top.
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // Scroll lock while the modal is open (save/restore, not toggle, so
    // nesting with any other overflow change can't corrupt body styles).
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    // Initial focus: the dialog surface itself (tabIndex=-1 below). The
    // step heading is announced through aria-labelledby, and the next Tab
    // lands on the first control of the current step.
    container.focus();

    // Focusables are queried at KEYDOWN time, not trapped in a closure —
    // the wizard's steps swap their controls constantly (and a disabled
    // Continue must drop out of the cycle the moment it disables).
    const focusables = () =>
      Array.from(
        container.querySelectorAll<HTMLElement>(
          [
            'a[href]',
            'button:not([disabled])',
            'input:not([disabled])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            '[tabindex]:not([tabindex="-1"])',
          ].join(', ')
        )
      ).filter((el) => el.getClientRects().length > 0); // visible only

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        container.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      const atEdge =
        current === container || !container.contains(current)
          ? 'either'
          : current === first
            ? 'first'
            : current === last
              ? 'last'
              : 'inside';
      if (e.shiftKey && (atEdge === 'first' || atEdge === 'either')) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (atEdge === 'last' || atEdge === 'either')) {
        e.preventDefault();
        first.focus();
      }
    };

    container.addEventListener('keydown', onKeyDown);
    return () => {
      container.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [active]);
  return ref;
}

export default function OnboardingWizard({
  onComplete,
  onClose,
  onUploadCv,
  initialHasCv,
  initialLanguages,
  initialIncludeRemote,
  initialCountry,
  initialRegion,
  initialMunicipality,
  initialMunicipalities,
  initialSearchRadiusKm,
  initialOccupations,
  initialQueries,
}: Props) {
  const [step, setStep] = useState(0);
  // Step 1 (Your CV): true once a CV is on file — seeded from
  // initialHasCv for "Edit setup" re-runs, set by this run's upload.
  // Gates the step: the AI titles step reads the CV server-side.
  const [cvReady, setCvReady] = useState(Boolean(initialHasCv));
  const handleCvUpload = useCallback(
    async (file: File) => {
      await onUploadCv(file); // throws on failure — CvUpload renders the error
      setCvReady(true);
    },
    [onUploadCv]
  );
  // FE-10: the wizard is a true modal — trap/restore focus + scroll lock
  // (see useFocusTrap above). Always active: the component only mounts
  // while the dialog is shown.
  const dialogRef = useFocusTrap(true);
  const titleId = useId();
  const [geo, setGeo] = useState<GeoData | null>(null);
  const [country, setCountry] = useState(initialCountry ?? '');
  const [region, setRegion] = useState(initialRegion ?? '');
  // STRICT multi-municipality scope: picking Malmö means Malmö; add Lund
  // for the commute belt. Empty selection = explicit whole region.
  const [municipalityList, setMunicipalityList] = useState<string[]>(
    initialMunicipalities ?? (initialMunicipality ? [initialMunicipality] : [])
  );
  const [remoteOnly, setRemoteOnly] = useState(false);
  // Commute zone (km) around the first chosen municipality — 0 = exact
  // towns only. Prefilled in edit mode so re-saving setup never
  // silently nulls an existing radius.
  const [searchRadiusKm, setSearchRadiusKm] = useState<number>(
    initialSearchRadiusKm ?? 0
  );
  const [includeRemote, setIncludeRemote] = useState(Boolean(initialIncludeRemote));
  const [languages, setLanguages] = useState<string[]>(initialLanguages ?? ['English']);
  const [mode, setMode] = useState<SearchMode>('field');
  const [directQueries, setDirectQueries] = useState<string[]>([]);
  const [pivotSuggestions, setPivotSuggestions] = useState<{ query: string; why: string }[]>([]);
  // SE taxonomy concepts: validated profession codes — each becomes a
  // search unit that catches ads whose title never contains the query
  // Seeded with the user's SAVED picks so they render as chips from
  // the first frame (visible, deselectable); fresh suggestions MERGE
  // on top, never replace.
  const [occupationSuggestions, setOccupationSuggestions] = useState<OccupationSuggestion[]>(
    initialOccupations ?? []
  );
  // Submit always sends this set — prefilled from storage so professions
  // survive setup edits and survive a failed/changed suggestion fetch.
  const [occSelected, setOccSelected] = useState<Set<string>>(
    new Set((initialOccupations ?? []).map((o) => o.code))
  );
  const [selected, setSelected] = useState<Set<string>>(new Set(initialQueries ?? []));
  const [customQueries, setCustomQueries] = useState<string[]>([]);
  const [customInput, setCustomInput] = useState('');
  const [loadingQueries, setLoadingQueries] = useState(false);
  const [saving, setSaving] = useState(false);
  // FE-21: a failed save-on-finish used to reject through the button into
  // an unhandled rejection — the spinner stopped and nothing else happened.
  // The wizard is a fullscreen modal, so the console's error banner behind
  // it is invisible: the error must show IN here.
  const [finishError, setFinishError] = useState<string | null>(null);

  useEffect(() => {
    getGeo().then(setGeo).catch(() => setGeo(null));
  }, []);

  const municipalities = useMemo(
    () => (geo && country && region ? (geo.geo[country]?.[region] ?? []) : []),
    [geo, country, region]
  );

  // Fetch AI suggestions when reaching the Job titles step (re-fetch when mode changes)
  // Auto-fetch runs ONCE per (country, mode) — tracked by key, never by
  // result-array contents, so a failed request cannot re-trigger itself
  // (the old pattern reset the arrays in catch, re-satisfying the guard
  // and looping forever). Failures surface a retry button instead.
  const [suggestError, setSuggestError] = useState(false);
  const fetchedKeyRef = useRef<string | null>(null);

  const loadSuggestions = useCallback(async () => {
    if (loadingQueries) return;
    setLoadingQueries(true);
    setSuggestError(false);
    try {
      const result = await suggestQueries(country, mode);
      setDirectQueries(result.from_your_experience);
      setPivotSuggestions(result.worth_a_look);
      setSelected(
        new Set([...result.from_your_experience, ...result.worth_a_look.map((p) => p.query)])
      );
      const occs = result.occupation_suggestions ?? [];
      // MERGE into the rendered list — never a replacement: a stored
      // profession must stay visible and selectable even when the
      // fresh call doesn't return it.
      setOccupationSuggestions((prev) => {
        const seen = new Set(prev.map((o) => o.code));
        return [...prev, ...occs.filter((o) => !seen.has(o.code))];
      });
      // Suggestions default ON, merged with anything already saved.
      setOccSelected((prev) => new Set([...prev, ...occs.map((o) => o.code)]));
      fetchedKeyRef.current = `${country}|${mode}`;
    } catch {
      setDirectQueries([]);
      setPivotSuggestions([]);
      // occupationSuggestions and occSelected deliberately survive:
      // saved professions stay rendered and selected through a GLM
      // blip during an unrelated edit.

      setSuggestError(true);
    } finally {
      setLoadingQueries(false);
    }
  }, [country, mode, loadingQueries]);

  useEffect(() => {
    const key = `${country}|${mode}`;
    if (step !== 4 || !country || fetchedKeyRef.current === key) return;
    // Edit mode with existing titles: keep the user's curated list — no
    // auto-overwrite; they can request fresh suggestions explicitly.
    if ((initialQueries?.length ?? 0) > 0 && fetchedKeyRef.current === null) {
      fetchedKeyRef.current = key;
      return;
    }
    loadSuggestions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, country, mode]);

  const changeMode = (m: SearchMode) => {
    if (m === mode) return;
    setMode(m);
    setDirectQueries([]); // trigger re-fetch with the new strategy
    setPivotSuggestions([]);
  };

  const allQueries = useMemo(
    () => [...directQueries, ...pivotSuggestions.map((p) => p.query), ...customQueries],
    [directQueries, pivotSuggestions, customQueries]
  );

  const toggle = (q: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(q)) next.delete(q);
      else next.add(q);
      return next;
    });

  const addCustom = () => {
    const q = customInput.trim();
    if (q && !allQueries.includes(q)) {
      setCustomQueries((prev) => [...prev, q]);
      setSelected((prev) => new Set(prev).add(q));
    }
    setCustomInput('');
  };

  const toggleLanguage = (lang: string) =>
    setLanguages((prev) => (prev.includes(lang) ? prev.filter((l) => l !== lang) : [...prev, lang]));

  // The radius control is honest only where it can anchor: the user's
  // PRIMARY town has a centroid (server-published list). TRI-STATE,
  // deliberately: geo===null means UNKNOWN (the /geo call failed),
  // not unsupported — an unknown must never drop a saved radius at
  // save (review finding: the guard used to conflate the two and a
  // single failed /geo call wiped the commute zone behind a hidden
  // control). The server's geo_plan is the real authority anyway: an
  // anchorless radius harmlessly falls back to municipality codes.
  const radiusAnchorSupported =
    country === 'SE' &&
    municipalityList.length > 0 &&
    geo !== null &&
    (geo.radius_supported ?? []).includes(municipalityList[0]);
  const radiusKnownUnsupported =
    geo !== null && !radiusAnchorSupported;

  const canProceed = [
    cvReady, // a CV must exist before anything downstream can work
    Boolean(country),
    Boolean(region), // municipality optional (whole region is valid)
    languages.length > 0,
    selected.size > 0 || occSelected.size > 0, // queries OR professions
    true,
  ][step];

  const finish = async () => {
    setSaving(true);
    setFinishError(null);
    try {
      await onComplete({
        country,
        region: region || null,
        municipalities: municipalityList,
        municipality: municipalityList[0] ?? null, // legacy single, kept in sync
        search_radius_km: !radiusKnownUnsupported && searchRadiusKm > 0 ? searchRadiusKm : null,
        remote_only: remoteOnly,
        include_remote: includeRemote || remoteOnly,
        search_queries: [...selected],
        occupation_codes: [...occSelected],
        languages,
      });
    } catch (err) {
      // FE-21: keep the wizard open with the user's picks intact; show
      // the failure inline (same panel pattern as the console's submit
      // errors). This also HANDLES the parent's rethrow — see
      // handleOnboardingComplete in app/page.tsx.
      setFinishError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-4 backdrop-blur-sm">
      <motion.div
        ref={dialogRef}
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="w-full max-w-xl rounded-2xl border border-line bg-surface shadow-2xl outline-none"
      >
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-line p-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-signal/30 bg-signal/10">
            <Radar className="h-5 w-5 text-signal" aria-hidden />
          </div>
          <div className="flex-1">
            <h2 id={titleId} className="font-semibold text-hi">Set up your job hunt</h2>
            <p className="text-xs text-low">Takes a minute — makes everything after it personal</p>
          </div>
          {onClose && (
            <button onClick={onClose} aria-label="Close" className="text-low transition-colors hover:text-mid">
              <X className="h-5 w-5" />
            </button>
          )}
        </div>

        {/* Stepper */}
        <div className="flex gap-1 px-5 pt-4">
          {STEPS.map((label, i) => (
            <div key={label} className="flex-1">
              <div
                className={cn(
                  'h-1 rounded-full',
                  i < step ? 'bg-ok' : i === step ? 'bg-signal' : 'bg-line-2'
                )}
              />
              <p className={cn('mt-1.5 text-[11px]', i === step ? 'text-mid' : 'text-low')}>
                {label}
              </p>
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="min-h-[300px] p-6">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
            >
              {step === 0 && (
                <div>
                  <StepTitle icon={<FileText className="h-4 w-4" />} title="First — your CV" />
                  <p className="mt-3 text-sm leading-relaxed text-mid">
                    Every job you see is scored against your CV — it&apos;s the
                    source of truth for your whole hunt. Have it ready as a PDF or Word (.docx).
                  </p>
                  <div className="mt-4">
                    <CvUpload onUploaded={handleCvUpload} hasExistingCv={cvReady} />
                  </div>
                  {cvReady && (
                    <p className="mt-3 flex items-center gap-1.5 text-sm text-ok">
                      <Check className="h-4 w-4" aria-hidden />
                      CV on file — continue when ready (or drop a new one to replace it).
                    </p>
                  )}
                </div>
              )}

              {step === 1 && (
                <div>
                  <StepTitle icon={<Globe2 className="h-4 w-4" />} title="Where are you job hunting?" />
                  <div className="mt-5 grid grid-cols-2 gap-3">
                    {(geo?.countries ?? []).map((c) => (
                      <button
                        key={c.code}
                        onClick={() => {
                          setCountry(c.code);
                          setRegion('');
                          setMunicipalityList([]);
                          // Swedish taxonomy concepts are SE-only —
                          // switching country must not carry them into
                          // the new profile (review finding: prefilled
                          // occSelected survived the switch).
                          setOccupationSuggestions([]);
                          setOccSelected(new Set());
                        }}
                        className={cn(
                          'rounded-xl border p-5 text-left transition-colors',
                          country === c.code
                            ? 'border-signal bg-signal/10'
                            : 'border-line hover:border-line-2'
                        )}
                      >
                        <span className="text-3xl" aria-hidden>{c.flag}</span>
                        <p className="mt-2 font-medium text-hi">{c.name}</p>
                        <p className="text-xs text-low">
                          {c.code === 'SE' ? 'Platsbanken — every public listing' : 'Reed.co.uk — every sector'}
                        </p>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 2 && (
                <div>
                  <StepTitle icon={<MapPin className="h-4 w-4" />} title="Which area should we search?" />
                  <div className="mt-5 space-y-4">
                    <label className="block">
                      <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">Region</span>
                      <select
                        value={region}
                        onChange={(e) => {
                          setRegion(e.target.value);
                          setMunicipalityList([]);
                        }}
                        className="w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors focus:border-signal"
                      >
                        <option value="">Select a region…</option>
                        {Object.keys(geo?.geo[country] ?? {}).map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    </label>
                    <div>
                      <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                        Cities / municipalities{' '}
                        <span className="normal-case">
                          (pick one or several — none selected means all of {region || 'the region'})
                        </span>
                      </span>
                      {!region ? (
                        <p className="rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-low">
                          Select a region first…
                        </p>
                      ) : (
                        <div
                          role="group"
                          aria-label="Municipalities"
                          className="flex max-h-40 flex-wrap gap-2 overflow-y-auto rounded-lg border border-line bg-ink p-2.5"
                        >
                          {municipalities.map((m) => {
                            const on = municipalityList.includes(m);
                            return (
                              <button
                                key={m}
                                type="button"
                                aria-pressed={on}
                                onClick={() =>
                                  setMunicipalityList((prev) =>
                                    prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]
                                  )
                                }
                                className={cn(
                                  'rounded-full border px-3 py-1.5 text-sm transition-colors',
                                  on
                                    ? 'border-signal bg-signal/15 text-hi'
                                    : 'border-line text-mid hover:border-line-2 hover:text-hi'
                                )}
                              >
                                {m}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                    {radiusAnchorSupported && (
                      <div>
                        <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                          Commute radius around {municipalityList[0]}{' '}
                          <span className="normal-case">
                            (catches nearby towns without picking them)
                          </span>
                        </span>
                        <div className="flex flex-wrap gap-2" role="group" aria-label="Commute radius">
                          {[
                            { km: 0, label: 'Selected towns only' },
                            { km: 15, label: '+15 km' },
                            { km: 30, label: '+30 km' },
                            { km: 50, label: '+50 km' },
                          ].map(({ km, label }) => (
                            <button
                              key={km}
                              type="button"
                              aria-pressed={(searchRadiusKm ?? 0) === km}
                              onClick={() => setSearchRadiusKm(km)}
                              className={cn(
                                'rounded-full border px-3 py-1.5 text-sm transition-colors',
                                (searchRadiusKm ?? 0) === km
                                  ? 'border-signal bg-signal/15 text-hi'
                                  : 'border-line text-mid hover:border-line-2 hover:text-hi'
                              )}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                        {(searchRadiusKm ?? 0) > 0 && (
                          <p className="mt-1.5 text-xs text-low">
                            Jobs within {searchRadiusKm} km of {municipalityList[0]} — the
                            neighbouring towns come to you.
                          </p>
                        )}
                      </div>
                    )}
                    <button
                      onClick={() => setIncludeRemote(!includeRemote)}
                      aria-pressed={includeRemote}
                      className="flex w-full items-center justify-between rounded-lg border border-line p-3 text-left transition-colors hover:border-line-2"
                    >
                      <div>
                        <p className="text-sm font-medium text-hi">Include remote jobs</p>
                        <p className="text-xs text-low">
                          Also search worldwide remote boards (remotive, jobicy…). Leave off to
                          search strictly your area.
                        </p>
                      </div>
                      <span
                        className={cn(
                          'h-6 w-11 shrink-0 rounded-full p-0.5 transition-colors',
                          includeRemote ? 'bg-signal' : 'bg-line-2'
                        )}
                      >
                        <span
                          className={cn(
                            'block h-5 w-5 rounded-full bg-hi transition-transform',
                            includeRemote && 'translate-x-5'
                          )}
                        />
                      </span>
                    </button>
                    <button
                      onClick={() => {
                        const next = !remoteOnly;
                        setRemoteOnly(next);
                        if (next) setIncludeRemote(true); // remote-only implies remote opt-in
                      }}
                      aria-pressed={remoteOnly}
                      className="flex w-full items-center justify-between rounded-lg border border-line p-3 text-left transition-colors hover:border-line-2"
                    >
                      <div>
                        <p className="text-sm font-medium text-hi">Remote jobs only</p>
                        <p className="text-xs text-low">Skip anything that requires being on-site</p>
                      </div>
                      <span
                        className={cn(
                          'h-6 w-11 shrink-0 rounded-full p-0.5 transition-colors',
                          remoteOnly ? 'bg-signal' : 'bg-line-2'
                        )}
                      >
                        <span
                          className={cn(
                            'block h-5 w-5 rounded-full bg-hi transition-transform',
                            remoteOnly && 'translate-x-5'
                          )}
                        />
                      </span>
                    </button>
                  </div>
                </div>
              )}

              {step === 3 && (
                <div>
                  <StepTitle icon={<Languages className="h-4 w-4" />} title="Which languages do you work in?" />
                  <p className="mt-2 text-sm text-low">
                    Jobs posted in other languages are filtered out before matching — English
                    always passes. You can change this anytime under Edit setup.
                  </p>
                  <div className="mt-5 flex flex-wrap gap-2">
                    {LANGUAGE_OPTIONS.map((lang) => {
                      const on = languages.includes(lang);
                      return (
                        <button
                          key={lang}
                          onClick={() => toggleLanguage(lang)}
                          aria-pressed={on}
                          className={cn(
                            'inline-flex items-center gap-1.5 rounded-full border px-3.5 py-2 text-sm transition-colors',
                            on
                              ? 'border-signal bg-signal/15 text-signal'
                              : 'border-line text-low hover:border-line-2 hover:text-mid'
                          )}
                        >
                          {on && <Check className="h-3.5 w-3.5" />}
                          {lang}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {step === 4 && (
                <div>
                  <StepTitle title="What roles should we hunt for?" />
                  {/* Strategy — always the user's own choice, never inferred */}
                  <div className="mt-4 space-y-2">
                    {MODES.map((m) => (
                      <button
                        key={m.id}
                        onClick={() => changeMode(m.id)}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors',
                          mode === m.id
                            ? 'border-signal bg-signal/10'
                            : 'border-line hover:border-line-2'
                        )}
                      >
                        <m.icon className="h-5 w-5 shrink-0 text-mid" aria-hidden />
                        <div className="flex-1">
                          <p className="text-sm font-medium text-hi">{m.label}</p>
                          <p className="text-xs text-low">{m.hint}</p>
                        </div>
                        {mode === m.id && <Check className="h-4 w-4 text-signal" />}
                      </button>
                    ))}
                  </div>

                  {!loadingQueries && directQueries.length === 0 && selected.size > 0 && (
                    <div className="mt-5 rounded-lg border border-line bg-ink/60 p-3">
                      <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                        Your current search titles ({selected.size})
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {[...selected].map((q) => (
                          <span key={q} className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs text-mid">
                            {q}
                          </span>
                        ))}
                      </div>
                      <button
                        onClick={() => { fetchedKeyRef.current = null; loadSuggestions(); }}
                        className="mt-3 text-xs text-signal transition-colors hover:text-signal/80"
                      >
                        Get fresh AI suggestions for this strategy →
                      </button>
                    </div>
                  )}
                  {suggestError && (
                    <div className="mt-5 rounded-lg bg-bad/10 p-3 text-sm text-hi" role="alert">
                      Couldn&apos;t load AI suggestions.{' '}
                      <button onClick={loadSuggestions} className="font-semibold text-signal hover:underline">
                        Try again
                      </button>
                    </div>
                  )}
                  {loadingQueries ? (
                    <div className="flex flex-col items-center py-10 text-low" role="status">
                      <Loader2 className="mb-3 h-6 w-6 animate-spin text-signal" />
                      <p className="text-sm">
                        {mode === 'widen'
                          ? 'Reading your CV\u2019s transferable skills…'
                          : 'AI is reading your CV…'}
                      </p>
                      <p className="text-xs">usually 10-45 seconds</p>
                    </div>
                  ) : (
                    <>
                      {directQueries.length > 0 && (
                        <div className="mt-5">
                          <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-low">
                            From your experience
                          </p>
                          <div className="flex flex-wrap gap-2">
                            {directQueries.map((q) => (
                              <QueryChip key={q} query={q} on={selected.has(q)} onToggle={() => toggle(q)} />
                            ))}
                          </div>
                        </div>
                      )}

                      {occupationSuggestions.length > 0 && (
                        <div className="mt-5">
                          <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ok/90">
                            Your professions — official job taxonomy{' '}
                            <span className="normal-case">
                              (catches jobs whose title uses other words)
                            </span>
                          </p>
                          <div className="flex flex-wrap gap-2">
                            {occupationSuggestions.map((o) => (
                              <button
                                key={o.code}
                                type="button"
                                aria-pressed={occSelected.has(o.code)}
                                onClick={() =>
                                  setOccSelected((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(o.code)) next.delete(o.code);
                                    else next.add(o.code);
                                    return next;
                                  })
                                }
                                className={cn(
                                  'rounded-full border px-3 py-1.5 text-sm transition-colors',
                                  occSelected.has(o.code)
                                    ? 'border-ok bg-ok/15 text-hi'
                                    : 'border-line text-mid hover:border-line-2 hover:text-hi'
                                )}
                              >
                                {o.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {pivotSuggestions.length > 0 && (
                        <div className="mt-5">
                          <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-info/90">
                            {mode === 'widen'
                              ? 'Jobs your skills open up'
                              : 'Worth a look — you might not have thought of these'}
                          </p>
                          <div className="space-y-2">
                            {pivotSuggestions.map((p) => (
                              <button
                                key={p.query}
                                onClick={() => toggle(p.query)}
                                className={cn(
                                  'flex w-full items-start gap-2.5 rounded-lg border p-2.5 text-left transition-colors',
                                  selected.has(p.query)
                                    ? 'border-info/50 bg-info/10'
                                    : 'border-line hover:border-line-2'
                                )}
                              >
                                <span
                                  className={cn(
                                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                                    selected.has(p.query)
                                      ? 'border-info bg-info/25 text-info'
                                      : 'border-line-2'
                                  )}
                                >
                                  {selected.has(p.query) && <Check className="h-3 w-3" />}
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="text-sm text-hi">{p.query}</span>
                                  {p.why && (
                                    <span className="block text-xs leading-snug text-low">{p.why}</span>
                                  )}
                                </span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="mt-5 flex gap-2">
                        <input
                          value={customInput}
                          onChange={(e) => setCustomInput(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && addCustom()}
                          placeholder="Add your own search title…"
                          className="flex-1 rounded-lg border border-line bg-ink px-3 py-2 text-sm text-hi outline-none transition-colors placeholder:text-low focus:border-signal"
                        />
                        <button
                          onClick={addCustom}
                          aria-label="Add search title"
                          className="rounded-lg border border-line px-3 py-2 text-sm text-mid transition-colors hover:border-line-2 hover:text-hi"
                        >
                          <Plus className="h-4 w-4" />
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}

              {step === 5 && (
                <div>
                  <StepTitle title="Ready — here's your setup" />
                  <div className="mt-5 space-y-2.5 rounded-xl border border-line bg-ink/60 p-4 text-sm">
                    <SummaryRow label="Country" value={`${flagFor(country)} ${nameFor(geo, country)}`} />
                    <SummaryRow
                      label="Area"
                      value={
                        (municipalityList.length
                          ? [...municipalityList]
                          : [`All of ${region}`]
                        ).join(', ') || 'everywhere'
                      }
                    />
                    <SummaryRow label="Remote" value={remoteOnly ? 'remote jobs only' : includeRemote ? 'local + remote' : 'strictly local'} />
                    <SummaryRow label="Languages" value={languages.join(', ')} />
                    <SummaryRow label="Strategy" value={MODES.find((m) => m.id === mode)?.label ?? mode} />
                    <SummaryRow label="Job titles" value={`${selected.size} search queries`} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {[...selected].map((q) => (
                      <span key={q} className="rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs text-mid">
                        {q}
                      </span>
                    ))}
                  </div>
                  <p className="mt-5 text-sm text-low">
                    Your first targeted hunt starts automatically — jobs will be scraped from
                    your country&apos;s boards, filtered to your area, and ranked against your CV.
                  </p>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {finishError && (
          <p className="mx-4 mt-2 rounded-lg bg-bad/10 p-3 text-sm text-bad" role="alert">
            Couldn&apos;t save your setup — nothing was lost, try again: {finishError}
          </p>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-line p-4">
          <button
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-mid transition-colors hover:text-hi disabled:opacity-30"
          >
            <ArrowLeft className="h-4 w-4" /> Back
          </button>
          <p className="num text-xs text-low">
            Step {step + 1} of {STEPS.length}
          </p>
          {step < STEPS.length - 1 ? (
            <button
              onClick={() => canProceed && setStep((s) => s + 1)}
              disabled={!canProceed || (step === 3 && loadingQueries)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-40"
            >
              Continue <ArrowRight className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={finish}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-4 py-2 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98] disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radar className="h-4 w-4" />}
              Start hunting
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
}

function StepTitle({ icon, title }: { icon?: React.ReactNode; title: string }) {
  return (
    <h3 className="flex items-center gap-2 text-lg font-semibold text-hi">
      {icon}
      {title}
    </h3>
  );
}

function QueryChip({ query, on, onToggle }: { query: string; on: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={on}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors',
        on
          ? 'border-signal/60 bg-signal/15 text-signal'
          : 'border-line text-low hover:border-line-2 hover:text-mid'
      )}
    >
      {on && <Check className="h-3.5 w-3.5" />}
      {query}
    </button>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-low">{label}</span>
      <span className="text-hi">{value}</span>
    </div>
  );
}

function flagFor(country: string): string {
  return country === 'SE' ? '🇸🇪' : country === 'GB' ? '🇬🇧' : '🌍';
}

function nameFor(geo: GeoData | null, country: string): string {
  return geo?.countries.find((c) => c.code === country)?.name ?? country;
}
