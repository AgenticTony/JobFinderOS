// The ai_jobs poll loop, kept free of axios so node --test can drive it.
// Give-up rule: time since the last SUCCESSFUL poll, not a failure count.
// Five failures at 2s told a phone that lost signal for ten seconds that
// a still-running job had failed (PR #153 review, 2026-11-11); a job that
// is really unreachable (pruned, persistent 5xx) still ends the wait.

export const POLL_GIVE_UP_MS = 60_000;

type PolledJob = { status: string };

export async function pollAiJob<J extends PolledJob>(
  job: J,
  fetchJob: (job: J) => Promise<J>,
  opts: {
    intervalMs?: number;
    giveUpAfterMs?: number;
    onProgress?: (job: J) => void;
    isCancelled?: () => boolean;
    sleep?: (ms: number) => Promise<void>;
    now?: () => number;
  } = {},
): Promise<J> {
  const intervalMs = opts.intervalMs ?? 2000;
  const giveUpAfterMs = opts.giveUpAfterMs ?? POLL_GIVE_UP_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const now = opts.now ?? Date.now;
  let current = job;
  let lastSuccess = now();
  while (current.status === 'queued' || current.status === 'running') {
    if (opts.isCancelled?.()) return current;
    opts.onProgress?.(current);
    await sleep(intervalMs);
    if (opts.isCancelled?.()) return current;
    try {
      current = await fetchJob(current);
      lastSuccess = now();
    } catch {
      // transient (cold start, network): keep waiting on the same job
      if (now() - lastSuccess >= giveUpAfterMs) {
        throw new Error('job status unreachable — try again');
      }
    }
  }
  return current;
}
