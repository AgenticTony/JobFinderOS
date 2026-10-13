import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Tier } from '@/types';
import type { ConsoleDict } from '@/i18n/console';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Labels are translated (dict console.tiers); only styling lives here.
export const TIER_CONFIG: Record<Tier, { color: string; ring: string }> = {
  excellent_match: { color: 'bg-ok/15 text-ok border-ok/30', ring: 'text-ok' },
  good_match: { color: 'bg-info/15 text-info border-info/30', ring: 'text-info' },
  stretch: { color: 'bg-signal/15 text-signal border-signal/30', ring: 'text-signal' },
  poor_match: { color: 'bg-bad/15 text-bad border-bad/30', ring: 'text-bad' },
};

export function scoreColor(score: number): string {
  if (score >= 80) return 'text-ok';
  if (score >= 50) return 'text-info';
  if (score >= 30) return 'text-signal';
  return 'text-bad';
}

/**
 * The API returns naive UTC datetimes (no offset suffix). Per ECMA-262,
 * `new Date()` treats an offsetless date-time as LOCAL time, skewing every
 * relative timestamp by the browser's UTC offset — so append 'Z' first.
 */
export function parseUtcDate(iso: string): Date {
  return new Date(/[Zz]$|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

// `tt` = the active language's console.time dict (useT().console.time).
// Deliberately required: a default would let a future caller compile
// while rendering English inside the Swedish console — invisible to
// tsc and the leftover-English scanner alike.
export function timeAgo(iso: string, tt: ConsoleDict['time']): string {
  const seconds = Math.floor((Date.now() - parseUtcDate(iso).getTime()) / 1000);
  if (seconds < 60) return tt.justNow;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return tt.minutes(minutes);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return tt.hours(hours);
  return tt.days(Math.floor(hours / 24));
}
