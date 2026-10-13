// Wizard work-rights prefill (bug 2026-10-13: "Edit setup" reset a saved
// answer to prefer_not_say, which then saved over it via onboarding).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialWizardWorkRights, wizardWorkRightsOptions, WIZARD_WORK_RIGHTS } from '../src/lib/workRights.ts';
import { dicts } from '../src/i18n/dict.ts';

test('re-running setup starts from the saved answer, not prefer_not_say', () => {
  assert.equal(initialWizardWorkRights('eu_right'), 'eu_right');
  assert.equal(initialWizardWorkRights('needs_sponsorship'), 'needs_sponsorship');
  assert.equal(initialWizardWorkRights(null), 'prefer_not_say');
  assert.equal(initialWizardWorkRights(undefined), 'prefer_not_say');
});

test('a saved answer outside the wizard list is still offered (and so stays selected)', () => {
  const opts = wizardWorkRightsOptions('permanent_resident');
  assert.ok(opts.includes('permanent_resident'));
  assert.equal(opts.at(-1), 'prefer_not_say');
  assert.deepEqual(wizardWorkRightsOptions('eu_right'), [...WIZARD_WORK_RIGHTS]);
  assert.deepEqual(wizardWorkRightsOptions(null), [...WIZARD_WORK_RIGHTS]);
});

test('every offerable code has a label in both languages', () => {
  for (const code of wizardWorkRightsOptions('permanent_resident')) {
    assert.ok(dicts.en.console.workRights[code], `en label for ${code}`);
    assert.ok(dicts.sv.console.workRights[code], `sv label for ${code}`);
  }
});
