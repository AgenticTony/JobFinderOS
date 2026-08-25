'use client';

// Onboarding wizard — first-time setup that personalizes the whole OS:
// country (source pack) -> region/city + remote (location filter) ->
// AI-suggested job titles from the CV (search queries) -> confirm.

import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Compass,
  Globe2,
  Loader2,
  MapPin,
  Plus,
  Radar,
  Search,
  Target,
  X,
} from 'lucide-react';
import { getGeo, suggestQueries } from '@/lib/api';
import type { GeoData, OnboardingPayload, SearchMode } from '@/types';
import { cn } from '@/lib/utils';

interface Props {
  onComplete: (payload: OnboardingPayload) => Promise<void>;
  onClose?: () => void; // optional — wizard is modal but re-openable from Profile
}

const STEPS = ['Country', 'Location', 'Job titles', 'Confirm'] as const;

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

export default function OnboardingWizard({ onComplete, onClose }: Props) {
  const [step, setStep] = useState(0);
  const [geo, setGeo] = useState<GeoData | null>(null);
  const [country, setCountry] = useState('');
  const [region, setRegion] = useState('');
  const [municipality, setMunicipality] = useState('');
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [mode, setMode] = useState<SearchMode>('field');
  const [directQueries, setDirectQueries] = useState<string[]>([]);
  const [pivotSuggestions, setPivotSuggestions] = useState<{ query: string; why: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [customQueries, setCustomQueries] = useState<string[]>([]);
  const [customInput, setCustomInput] = useState('');
  const [loadingQueries, setLoadingQueries] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getGeo().then(setGeo).catch(() => setGeo(null));
  }, []);

  const municipalities = useMemo(
    () => (geo && country && region ? (geo.geo[country]?.[region] ?? []) : []),
    [geo, country, region]
  );

  // Fetch AI suggestions when reaching step 3 (re-fetch when mode changes)
  useEffect(() => {
    if (step !== 2 || !country || loadingQueries) return;
    if (directQueries.length > 0 || pivotSuggestions.length > 0) return; // already loaded for this mode
    setLoadingQueries(true);
    suggestQueries(country, mode)
      .then((result) => {
        setDirectQueries(result.from_your_experience);
        setPivotSuggestions(result.worth_a_look);
        setSelected(
          new Set([...result.from_your_experience, ...result.worth_a_look.map((p) => p.query)])
        );
      })
      .catch(() => {
        setDirectQueries([]);
        setPivotSuggestions([]);
      })
      .finally(() => setLoadingQueries(false));
  }, [step, country, mode, directQueries.length, pivotSuggestions.length, loadingQueries]);

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

  const canProceed = [
    Boolean(country),
    Boolean(region), // municipality optional (whole region is valid)
    selected.size > 0,
    true,
  ][step];

  const finish = async () => {
    setSaving(true);
    try {
      await onComplete({
        country,
        region: region || null,
        municipality: municipality || null,
        remote_only: remoteOnly,
        search_queries: [...selected],
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-4 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        role="dialog"
        aria-modal="true"
        aria-label="Set up your job hunt"
        className="w-full max-w-xl rounded-2xl border border-line bg-surface shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-line p-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-signal/30 bg-signal/10">
            <Radar className="h-5 w-5 text-signal" aria-hidden />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-hi">Set up your job hunt</h2>
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
                  <StepTitle icon={<Globe2 className="h-4 w-4" />} title="Where are you job hunting?" />
                  <div className="mt-5 grid grid-cols-2 gap-3">
                    {(geo?.countries ?? []).map((c) => (
                      <button
                        key={c.code}
                        onClick={() => {
                          setCountry(c.code);
                          setRegion('');
                          setMunicipality('');
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
                          {c.code === 'SE' ? 'Platsbanken + remote boards' : 'Reed + Adzuna + remote boards'}
                        </p>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 1 && (
                <div>
                  <StepTitle icon={<MapPin className="h-4 w-4" />} title="Which area should we search?" />
                  <div className="mt-5 space-y-4">
                    <label className="block">
                      <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">Region</span>
                      <select
                        value={region}
                        onChange={(e) => {
                          setRegion(e.target.value);
                          setMunicipality('');
                        }}
                        className="w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors focus:border-signal"
                      >
                        <option value="">Select a region…</option>
                        {Object.keys(geo?.geo[country] ?? {}).map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[10px] uppercase tracking-[0.14em] text-low">
                        City / municipality <span className="normal-case">(optional — whole region is fine)</span>
                      </span>
                      <select
                        value={municipality}
                        onChange={(e) => setMunicipality(e.target.value)}
                        disabled={!region}
                        className="w-full rounded-lg border border-line bg-ink px-3 py-2.5 text-sm text-hi outline-none transition-colors focus:border-signal disabled:opacity-40"
                      >
                        <option value="">All of {region || 'region'}…</option>
                        {municipalities.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                    <button
                      onClick={() => setRemoteOnly(!remoteOnly)}
                      aria-pressed={remoteOnly}
                      className="flex w-full items-center justify-between rounded-lg border border-line p-3 text-left transition-colors hover:border-line-2"
                    >
                      <div>
                        <p className="text-sm font-medium text-hi">Remote jobs only</p>
                        <p className="text-xs text-low">Skip anything that requires being on-site</p>
                      </div>
                      <span
                        className={cn(
                          'h-6 w-11 rounded-full p-0.5 transition-colors',
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

              {step === 2 && (
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

              {step === 3 && (
                <div>
                  <StepTitle title="Ready — here's your setup" />
                  <div className="mt-5 space-y-2.5 rounded-xl border border-line bg-ink/60 p-4 text-sm">
                    <SummaryRow label="Country" value={`${flagFor(country)} ${nameFor(geo, country)}`} />
                    <SummaryRow label="Area" value={[municipality, region].filter(Boolean).join(', ') || 'everywhere'} />
                    <SummaryRow label="Remote" value={remoteOnly ? 'remote jobs only' : 'on-site + remote'} />
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
              disabled={!canProceed || (step === 2 && loadingQueries)}
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
