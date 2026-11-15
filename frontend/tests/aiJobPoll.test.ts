// waitForAiJob's give-up rule (PR #153 review, 2026-11-11): five failed
// polls at 2s = ~10s, so a phone dropping signal briefly was told a job
// failed that was still running server-side. The limit is time without
// a successful poll, not a failure count.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pollAiJob, POLL_GIVE_UP_MS } from '../src/lib/aiJobPoll.ts';

type Job = { id: number; status: string };

function clock() {
  let t = 0;
  return { now: () => t, sleep: async (ms: number) => { t += ms; } };
}

test('a 20-second outage does not fail a job that then finishes', async () => {
  const c = clock();
  let calls = 0;
  const fetchJob = async (): Promise<Job> => {
    calls += 1;
    if (c.now() < 20_000) throw new Error('offline');
    return { id: 1, status: 'done' };
  };
  const done = await pollAiJob({ id: 1, status: 'queued' }, fetchJob, { now: c.now, sleep: c.sleep });
  assert.equal(done.status, 'done');
  assert.ok(calls > 5, `only ${calls} polls — the old 5-failure rule would have thrown`);
});

test('gives up once no poll has succeeded for the whole window', async () => {
  const c = clock();
  const fetchJob = async (): Promise<Job> => { throw new Error('gone'); };
  await assert.rejects(
    pollAiJob({ id: 1, status: 'running' }, fetchJob, { now: c.now, sleep: c.sleep }),
    /unreachable/,
  );
  assert.ok(c.now() >= POLL_GIVE_UP_MS, `gave up after ${c.now()}ms`);
  assert.ok(c.now() < POLL_GIVE_UP_MS + 5_000, `kept polling to ${c.now()}ms`);
});

test('a successful poll resets the window', async () => {
  const c = clock();
  const fetchJob = async (): Promise<Job> => {
    const t = c.now();
    if (t >= 100_000) return { id: 1, status: 'done' };
    if (t % 50_000 < 2_000) return { id: 1, status: 'running' };
    throw new Error('blip');
  };
  const done = await pollAiJob({ id: 1, status: 'running' }, fetchJob, { now: c.now, sleep: c.sleep });
  assert.equal(done.status, 'done');
});

test('cancellation returns without throwing', async () => {
  const c = clock();
  let cancelled = false;
  const fetchJob = async (): Promise<Job> => { cancelled = true; throw new Error('x'); };
  const out = await pollAiJob({ id: 1, status: 'queued' }, fetchJob,
    { now: c.now, sleep: c.sleep, isCancelled: () => cancelled });
  assert.equal(out.status, 'queued');
});
