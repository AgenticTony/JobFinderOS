// Wizard titles step (PR #153 review, 2026-11-11): the manual input is
// usable while suggestions load, but their arrival replaced the whole
// selection — a title typed during the wait stayed listed yet unticked,
// so it silently left the saved search.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeSuggestedSelection } from '../src/lib/titleSelection.ts';

test('a custom title added while loading stays selected', () => {
  const out = mergeSuggestedSelection(new Set(['My own title']), ['My own title'], ['Kemist', 'Laborant']);
  assert.deepEqual([...out].sort(), ['Kemist', 'Laborant', 'My own title']);
});

test('a custom title the user unticked stays unticked', () => {
  const out = mergeSuggestedSelection(new Set<string>(), ['My own title'], ['Kemist']);
  assert.deepEqual([...out], ['Kemist']);
});

test("the previous strategy's suggestions do not carry over", () => {
  const out = mergeSuggestedSelection(new Set(['Old suggestion']), [], ['New suggestion']);
  assert.deepEqual([...out], ['New suggestion']);
});
