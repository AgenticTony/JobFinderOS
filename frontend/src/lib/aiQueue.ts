// WO-31: honest copy for queued AI work. Provider load never shows an
// error — the user sees where their request is and roughly how long.
import type { AiJob } from '@/types';
import { parseUtcDate } from '@/lib/utils';

export interface QueueCopy {
  about: (min: number | null) => string;
  ahead: (n: number) => string;
  paused: (time: string) => string;
}

/** "about N min" + "N ahead" (only when > 0), or the paused-until line. */
export function queueDetail(q: QueueCopy, job: AiJob): { about: string; extra: string | null } {
  if (job.paused_until) {
    const time = parseUtcDate(job.paused_until).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
    return { about: '', extra: q.paused(time) };
  }
  if (job.status !== 'queued') return { about: '', extra: null };
  return {
    about: q.about(job.eta_minutes),
    extra: job.position > 0 ? q.ahead(job.position) : null,
  };
}

export const isPending = (job: AiJob | null | undefined): job is AiJob =>
  Boolean(job && (job.status === 'queued' || job.status === 'running'));
