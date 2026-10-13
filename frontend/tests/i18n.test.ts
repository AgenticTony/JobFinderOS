// i18n invariants — run with `node --test tests/` (Node 24 strips types).
// The compiler already proves sv has every key of en for plain objects;
// these tests cover what it can't: Record-typed maps (a missing sv key
// type-checks), empty strings, function entries' output, and the locale
// resolution rules the console depends on.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dicts } from '../src/i18n/dict.ts';
import { resolveLocale } from '../src/i18n/locale.ts';
import { splitRich } from '../src/i18n/richText.ts';

type Node = unknown;

// Sample arguments per arity — every dictionary function takes numbers,
// strings, or (approved: boolean, err: string). Called with BOTH a number
// and a string so any arity/type mix yields a string, never a throw.
const SAMPLES: unknown[][] = [[], [3], [3, '07'], [3, '07', 'x']];
// Functions whose parameters aren't (number|string) get explicit samples.
const OVERRIDES: Record<string, unknown[][]> = {
  'dict.console.shell.decisionFailed': [[true, 'ERR'], [false, 'ERR']],
};
// Intentionally empty in BOTH languages (a processor row with no region).
const EMPTY_OK = /privacy\.sections\[\d+\]\.blocks\[\d+\]\.items\[\d+\]\.where$/;

function walk(en: Node, sv: Node, path: string, problems: string[]) {
  if (typeof en === 'function') {
    if (typeof sv !== 'function') {
      problems.push(`${path}: en is a function, sv is ${typeof sv}`);
      return;
    }
    if (en.length !== sv.length) {
      problems.push(`${path}: arity en=${en.length} sv=${sv.length}`);
    }
    const args = SAMPLES[en.length] ?? [];
    const runs = OVERRIDES[path] ?? [args, args.map((x) => (typeof x === 'number' ? 1 : 'Malmö'))];
    for (const a of runs) {
      const outEn = en(...a);
      const outSv = (sv as (...x: unknown[]) => unknown)(...a);
      if (typeof outEn !== 'string' || !outEn.trim()) problems.push(`${path}: en(${a}) empty`);
      if (typeof outSv !== 'string' || !outSv.trim()) problems.push(`${path}: sv(${a}) empty`);
      // Every interpolated argument must survive into BOTH languages —
      // a translation that drops "{n}" silently lies about counts.
      for (const x of a) {
        if (typeof x === 'boolean') continue;
        const s = String(x);
        if (typeof outEn === 'string' && !outEn.includes(s)) problems.push(`${path}: en drops arg ${s}`);
        if (typeof outSv === 'string' && !outSv.includes(s)) problems.push(`${path}: sv drops arg ${s}`);
      }
    }
    return;
  }
  if (typeof en === 'string') {
    if (typeof sv !== 'string') problems.push(`${path}: en string, sv ${typeof sv}`);
    else if (!(EMPTY_OK.test(path) && !en && !sv)) {
      if (!sv.trim()) problems.push(`${path}: sv empty`);
      if (!en.trim()) problems.push(`${path}: en empty`);
    }
    if (typeof sv === 'string' && (en.match(/\*\*/g) ?? []).length !== (sv.match(/\*\*/g) ?? []).length) {
      problems.push(`${path}: emphasis markers differ`);
    }
    return;
  }
  if (Array.isArray(en)) {
    if (!Array.isArray(sv) || sv.length !== en.length) {
      problems.push(`${path}: array length en=${en.length} sv=${Array.isArray(sv) ? sv.length : typeof sv}`);
      return;
    }
    en.forEach((v, i) => walk(v, sv[i], `${path}[${i}]`, problems));
    return;
  }
  if (en && typeof en === 'object') {
    if (!sv || typeof sv !== 'object') {
      problems.push(`${path}: en object, sv ${typeof sv}`);
      return;
    }
    const ek = Object.keys(en).sort();
    const sk = Object.keys(sv).sort();
    for (const k of ek) if (!sk.includes(k)) problems.push(`${path}.${k}: missing in sv`);
    for (const k of sk) if (!ek.includes(k)) problems.push(`${path}.${k}: extra in sv`);
    for (const k of ek) if (sk.includes(k)) walk((en as never)[k], (sv as never)[k], `${path}.${k}`, problems);
    return;
  }
  if (en !== sv && typeof en !== typeof sv) problems.push(`${path}: type en=${typeof en} sv=${typeof sv}`);
}

test('sv mirrors en exactly: keys, arrays, functions, emphasis, no empties', () => {
  const problems: string[] = [];
  walk(dicts.en, dicts.sv, 'dict', problems);
  assert.deepEqual(problems, []);
});

test('the parity walker catches a missing Record key (the gap tsc leaves)', () => {
  const problems: string[] = [];
  const sv = structuredClone({ workRights: { a: 'x' } });
  walk({ workRights: { a: 'x', b: 'y' } }, sv, 'probe', problems);
  assert.deepEqual(problems, ['probe.workRights.b: missing in sv']);
});

test('the parity walker catches a translation that drops an argument', () => {
  const problems: string[] = [];
  walk({ f: (n: number) => `${n} jobs` }, { f: (_n: number) => 'jobb' }, 'probe', problems);
  assert.ok(problems.some((p) => p.includes('sv drops arg 3')), problems.join('\n'));
});

test('sv console strings are actually Swedish, not copies of en', () => {
  // Guards against "translated" sections that are English paste-throughs.
  // Allowed identical values: brand/format tokens that read the same.
  const SAME_OK = new Set(['PDF', 'Gmail', 'Region', 'live', 'JobFinderOS']);
  const copies: string[] = [];
  const scan = (en: Node, sv: Node, path: string) => {
    if (typeof en === 'string' && typeof sv === 'string') {
      if (en === sv && en.length > 3 && !SAME_OK.has(en) && !/^[+\d\s%·km]+$/.test(en)) copies.push(`${path}: "${en}"`);
    } else if (en && typeof en === 'object' && sv && typeof sv === 'object') {
      for (const k of Object.keys(en)) {
        // Structural discriminants and processor regions (proper nouns)
        // are legitimately identical across languages.
        if (k === 'kind' || k === 'tone' || k === 'n') continue;
        if (k === 'where' && /^EU \(/.test((en as never)[k])) continue;
        scan((en as never)[k], (sv as never)[k], `${path}.${k}`);
      }
    }
  };
  for (const section of ['console', 'wizard', 'privacy'] as const) {
    scan(dicts.en[section], dicts.sv[section], section);
  }
  assert.deepEqual(copies, []);
});

test('resolveLocale: saved choice wins over the browser', () => {
  assert.equal(resolveLocale('en', ['sv-SE']), 'en');
  assert.equal(resolveLocale('sv', ['en-GB']), 'sv');
});

test('resolveLocale: no saved choice → Swedish browsers get sv, everyone else en', () => {
  assert.equal(resolveLocale(null, ['sv-SE', 'en']), 'sv');
  assert.equal(resolveLocale(null, ['en-GB', 'sv']), 'sv'); // any listed sv counts, as on the landing
  assert.equal(resolveLocale(null, ['SV']), 'sv');
  assert.equal(resolveLocale(null, ['en-US', 'de']), 'en');
  assert.equal(resolveLocale(null, []), 'en');
});

test('splitRich: emphasis segments in order, empties dropped', () => {
  assert.deepEqual(splitRich('No CV — upload in **Profile** to start.'), [
    { text: 'No CV — upload in ', strong: false },
    { text: 'Profile', strong: true },
    { text: ' to start.', strong: false },
  ]);
  assert.deepEqual(splitRich('**Export.** In **Settings**'), [
    { text: 'Export.', strong: true },
    { text: ' In ', strong: false },
    { text: 'Settings', strong: true },
  ]);
  assert.deepEqual(splitRich('plain'), [{ text: 'plain', strong: false }]);
});

test('wizard step labels match the wizard\'s six steps in both languages', () => {
  // OnboardingWizard's STEP_COUNT and its canProceed gate both assume six.
  assert.equal(dicts.en.wizard.steps.length, 6);
  assert.equal(dicts.sv.wizard.steps.length, 6);
});

test('copy that names another UI label uses that label, in both languages', () => {
  // "Upload your CV in Profile", "press Prepare application", "change it
  // under Edit setup", "Settings → Your data": instructions that point at
  // a control must name it exactly as it renders, or they point at nothing.
  for (const locale of ['en', 'sv'] as const) {
    const c = dicts[locale].console;
    const w = dicts[locale].wizard;
    const p = dicts[locale].privacy;
    const refs: [string, string, string][] = [
      ['dashboard.noCv → nav.profile', c.dashboard.noCv, c.nav.profile],
      ['apps.emptyReviewBody → matchCard.prepare', c.apps.emptyReviewBody, c.matchCard.prepare],
      ['wizard.languages.body → profile.editSetup', w.languages.body, c.profile.editSetup],
      ['wizard.rights.body → nav.profile', w.rights.body, c.nav.profile],
      ['dashboard.matchingNow → dashboard.nextDecisions', c.dashboard.matchingNow, c.dashboard.nextDecisions],
      ['privacy section 01 → nav.settings', p.sections[0].blocks[1].kind === 'p' ? p.sections[0].blocks[1].text : '', `${c.nav.settings} → ${c.data.title}`],
    ];
    for (const [name, text, label] of refs) {
      assert.ok(text.includes(label), `${locale}: ${name} — "${label}" not found in "${text}"`);
    }
  }
});
