'use client';

// Onboarding wizard — first-time setup that personalizes the whole OS:
// country (source pack) -> region/city + remote (location filter) ->
// AI-suggested job titles from the CV (search queries) -> confirm.

import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowLeft, ArrowRight, Check, Globe2, Loader2, MapPin, Plus, Radar, X } from 'lucide-react';
import { getGeo, suggestQueries } from '@/lib/api';
import type { GeoData, OnboardingPayload, SearchMode } from '@/types';
import { cn } from '@/lib/utils';

interface Props {
  onComplete: (payload: OnboardingPayload) => Promise<void>;
  onClose?: () => void; // optional — wizard is modal but re-openable from Profile
}

const STEPS = ['Country', 'Location', 'Job titles', 'Confirm'] as const;

const MODES: { id: SearchMode; label: string; hint: string; icon: string }[] = [
  { id: 'field', label: 'Stay in my field', hint: 'Job titles from my CV', icon: '🎯' },
  { id: 'adjacent', label: 'Open to adjacent roles', hint: 'My field, plus near-neighbours', icon: '🔍' },
  { id: 'widen', label: 'Widen my options', hint: 'My field is shrinking or I\u2019m changing direction — use my transferable skills', icon: '🧭' },
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-xl rounded-2xl border border-white/10 bg-zinc-950 shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-white/10 p-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-sky-500 to-violet-600">
            <Radar className="h-5 w-5 text-white" />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-zinc-100">Set up your job hunt</h2>
            <p className="text-xs text-zinc-500">Takes a minute — makes everything after it personal</p>
          </div>
          {onClose && (
            <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
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
                  i < step ? 'bg-emerald-500' : i === step ? 'bg-sky-500' : 'bg-white/10'
                )}
              />
              <p className={cn('mt-1.5 text-[11px]', i === step ? 'text-zinc-300' : 'text-zinc-600')}>
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
                          'rounded-xl border p-5 text-left transition',
                          country === c.code
                            ? 'border-sky-500 bg-sky-500/10'
                            : 'border-white/10 hover:border-white/25'
                        )}
                      >
                        <span className="text-3xl">{c.flag}</span>
                        <p className="mt-2 font-medium text-zinc-100">{c.name}</p>
                        <p className="text-xs text-zinc-500">
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
                      <span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">Region</span>
                      <select
                        value={region}
                        onChange={(e) => {
                          setRegion(e.target.value);
                          setMunicipality('');
                        }}
                        className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2.5 text-sm outline-none focus:border-sky-500"
                      >
                        <option value="">Select a region…</option>
                        {Object.keys(geo?.geo[country] ?? {}).map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">
                        City / municipality <span className="normal-case text-zinc-600">(optional — whole region is fine)</span>
                      </span>
                      <select
                        value={municipality}
                        onChange={(e) => setMunicipality(e.target.value)}
                        disabled={!region}
                        className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2.5 text-sm outline-none focus:border-sky-500 disabled:opacity-40"
                      >
                        <option value="">All of {region || 'region'}…</option>
                        {municipalities.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                    <button
                      onClick={() => setRemoteOnly(!remoteOnly)}
                      className="flex w-full items-center justify-between rounded-lg border border-white/10 p-3 text-left hover:border-white/25"
                    >
                      <div>
                        <p className="text-sm font-medium text-zinc-200">Remote jobs only</p>
                        <p className="text-xs text-zinc-500">Skip anything that requires being on-site</p>
                      </div>
                      <span
                        className={cn(
                          'h-6 w-11 rounded-full p-0.5 transition',
                          remoteOnly ? 'bg-sky-500' : 'bg-white/15'
                        )}
                      >
                        <span
                          className={cn(
                            'block h-5 w-5 rounded-full bg-white transition',
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
                          'flex w-full items-center gap-3 rounded-lg border p-3 text-left transition',
                          mode === m.id
                            ? 'border-sky-500 bg-sky-500/10'
                            : 'border-white/10 hover:border-white/25'
                        )}
                      >
                        <span className="text-xl">{m.icon}</span>
                        <div className="flex-1">
                          <p className="text-sm font-medium text-zinc-200">{m.label}</p>
                          <p className="text-xs text-zinc-500">{m.hint}</p>
                        </div>
                        {mode === m.id && <Check className="h-4 w-4 text-sky-400" />}
                      </button>
                    ))}
                  </div>

                  {loadingQueries ? (
                    <div className="flex flex-col items-center py-10 text-zinc-500">
                      <Loader2 className="mb-3 h-6 w-6 animate-spin text-sky-400" />
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
                          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
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
                          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-violet-400/80">
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
                                  'flex w-full items-start gap-2.5 rounded-lg border p-2.5 text-left transition',
                                  selected.has(p.query)
                                    ? 'border-violet-500/50 bg-violet-500/10'
                                    : 'border-white/10 hover:border-white/25'
                                )}
                              >
                                <span
                                  className={cn(
                                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                                    selected.has(p.query)
                                      ? 'border-violet-500 bg-violet-500/30 text-violet-300'
                                      : 'border-white/20'
                                  )}
                                >
                                  {selected.has(p.query) && <Check className="h-3 w-3" />}
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="text-sm text-zinc-200">{p.query}</span>
                                  {p.why && (
                                    <span className="block text-xs leading-snug text-zinc-500">{p.why}</span>
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
                          className="flex-1 rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm outline-none focus:border-sky-500"
                        />
                        <button
                          onClick={addCustom}
                          className="rounded-lg border border-white/15 px-3 py-2 text-sm text-zinc-300 hover:bg-white/5"
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
                  <div className="mt-5 space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-4 text-sm">
                    <SummaryRow label="Country" value={`${flagFor(country)} ${nameFor(geo, country)}`} />
                    <SummaryRow label="Area" value={[municipality, region].filter(Boolean).join(', ') || 'everywhere'} />
                    <SummaryRow label="Remote" value={remoteOnly ? 'remote jobs only' : 'on-site + remote'} />
                    <SummaryRow label="Strategy" value={MODES.find((m) => m.id === mode)?.label ?? mode} />
                    <SummaryRow label="Job titles" value={`${selected.size} search queries`} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {[...selected].map((q) => (
                      <span key={q} className="rounded-full bg-sky-500/10 px-2.5 py-1 text-xs text-sky-300">
                        {q}
                      </span>
                    ))}
                  </div>
                  <p className="mt-5 text-sm text-zinc-500">
                    Your first targeted pipeline run starts automatically — jobs will be scraped from
                    your country&apos;s boards, filtered to your area, and ranked against your CV.
                  </p>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-white/10 p-4">
          <button
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-zinc-400 hover:text-zinc-200 disabled:opacity-30"
          >
            <ArrowLeft className="h-4 w-4" /> Back
          </button>
          <p className="text-xs text-zinc-600">
            Step {step + 1} of {STEPS.length}
          </p>
          {step < STEPS.length - 1 ? (
            <button
              onClick={() => canProceed && setStep((s) => s + 1)}
              disabled={!canProceed || (step === 2 && loadingQueries)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40"
            >
              Continue <ArrowRight className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={finish}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
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
    <h3 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
      {icon}
      {title}
    </h3>
  );
}

function QueryChip({ query, on, onToggle }: { query: string; on: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition',
        on
          ? 'border-sky-500/50 bg-sky-500/15 text-sky-200'
          : 'border-white/10 text-zinc-500 hover:border-white/25'
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
      <span className="text-zinc-500">{label}</span>
      <span className="text-zinc-200">{value}</span>
    </div>
  );
}

function flagFor(country: string): string {
  return country === 'SE' ? '🇸🇪' : country === 'GB' ? '🇬🇧' : '🌍';
}

function nameFor(geo: GeoData | null, country: string): string {
  return geo?.countries.find((c) => c.code === country)?.name ?? country;
}
