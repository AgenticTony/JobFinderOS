// Leftover-English scanner: every user-visible string in the translated
// surfaces must come from the dictionaries. Catches the regression the
// type system can't — someone adding `<p>Some new text</p>` to the
// console in English only.
//
// Heuristic, deliberately strict. Flags: JSX text nodes, user-facing
// attributes (title/placeholder/aria-label/alt/label), and capitalised
// quoted literals. A line may opt out with a trailing `// i18n-ok: why`
// (brand names, stored API values, ToS-mandated attribution).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = join(import.meta.dirname, '..', 'src');
export const FILES = [
  'app/app/page.tsx',
  'app/error.tsx',
  'app/privacy/page.tsx',
  'app/sv/privacy/page.tsx',
  'components/PrivacyView.tsx',
  'components/Sidebar.tsx',
  'components/MatchCard.tsx',
  'components/HuntPulse.tsx',
  'components/NextHunt.tsx',
  'components/CvUpload.tsx',
  'components/TierBadge.tsx',
  'components/OnboardingWizard.tsx',
];

// Tokens that read the same in both languages.
const ALLOWED_TEXT = new Set(['JobFinderOS', '—', '·', '•', '→', '/', '+', '…']);

export function scan(source: string): string[] {
  const findings: string[] = [];
  const lines = source.split('\n');
  let inBlock = false;
  lines.forEach((raw, i) => {
    if (/i18n-ok/.test(raw)) return;
    let line = raw;
    // strip block comments (incl. JSX {/* */}) spanning lines
    if (inBlock) {
      const end = line.indexOf('*/');
      if (end === -1) return;
      line = line.slice(end + 2);
      inBlock = false;
    }
    line = line.replace(/\{?\/\*.*?\*\/\}?/g, '');
    const open = line.indexOf('/*');
    if (open !== -1) {
      inBlock = true;
      line = line.slice(0, open);
    }
    line = line.replace(/(^|[^:'"`])\/\/.*$/, '$1'); // line comments (not URLs)
    if (/^\s*(import|export \{|export type|type |\|)/.test(line)) return;

    const where = `L${i + 1}: ${raw.trim().slice(0, 90)}`;
    // 1. JSX text between tags on one line: >Some text<
    for (const m of line.matchAll(/(?<![=-])>([^<>{}]*[A-Za-z]{2,}[^<>{}]*)</g)) {
      const txt = m[1].trim();
      if (txt && !ALLOWED_TEXT.has(txt) && !/=>|&&|\|\||\?\s|^\w+\.\w+/.test(txt)) findings.push(where);
    }
    // 2. A line that is ONLY prose (multi-line JSX text node)
    if (/^\s*[A-Z][A-Za-z&;'’]*(\s+[A-Za-z0-9&;'’,.—–()-]+){2,}[.…:]?\s*$/.test(line) && !/[=;{}]|^\s*[A-Z_]+\s*$/.test(line.replace(/&\w+;/g, ''))) {
      findings.push(where);
    }
    // 3. user-facing attributes with literal text
    for (const m of line.matchAll(/\b(title|placeholder|aria-label|alt|label)=["']([^"']*[A-Za-z]{2,}[^"']*)["']/g)) {
      if (!ALLOWED_TEXT.has(m[2])) findings.push(where);
    }
    // 4. capitalised prose literals: 'Word word…' / "Word…" / `Word ${x}`
    for (const m of line.matchAll(/(['"`])([A-Z][a-z]+[^'"`]*?)\1/g)) {
      const lit = m[2];
      if (ALLOWED_TEXT.has(lit)) continue;
      if (/^[A-Z][a-zA-Z]+$/.test(lit) && !/\s/.test(lit)) {
        // single capitalised word: flag only if it's a known UI word shape
        if (!/^(Retry|Copy|Approve|Pass|Back|Continue|Close|Saved|Sent|Settings|Profile|Dashboard|Matches|Applications|Region|Country|Area|Remote|Languages|Strategy)$/.test(lit)) continue;
      }
      findings.push(where);
    }
  });
  return [...new Set(findings)];
}

test('scanner flags hard-coded English (proof it can fail)', () => {
  const sample = [
    '<p className="x">Nothing sent yet</p>',
    '<input placeholder="Add your own search title…" />',
    "setError('Retry failed');",
    '        Couldn&apos;t reach the server — your lists may be missing.',
    '<span>{t.ok}</span>',
    "const brand = 'JobFinderOS';",
    "<b>Fine</b> // i18n-ok: test",
  ].join('\n');
  const f = scan(sample);
  assert.equal(f.length, 4, f.join('\n'));
});

for (const file of FILES) {
  test(`no hard-coded English in ${file}`, () => {
    let src: string;
    try {
      src = readFileSync(join(ROOT, file), 'utf8');
    } catch {
      assert.fail(`${file} missing — update FILES`);
    }
    assert.deepEqual(scan(src), []);
  });
}
