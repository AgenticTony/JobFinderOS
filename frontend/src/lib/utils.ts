import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Tier } from '@/types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const TIER_CONFIG: Record<Tier, { label: string; color: string; ring: string }> = {
  excellent_match: { label: 'Excellent Match', color: 'bg-ok/15 text-ok border-ok/30', ring: 'text-ok' },
  good_match: { label: 'Good Match', color: 'bg-info/15 text-info border-info/30', ring: 'text-info' },
  stretch: { label: 'Stretch', color: 'bg-signal/15 text-signal border-signal/30', ring: 'text-signal' },
  poor_match: { label: 'Poor Match', color: 'bg-bad/15 text-bad border-bad/30', ring: 'text-bad' },
};

export function scoreColor(score: number): string {
  if (score >= 80) return 'text-ok';
  if (score >= 50) return 'text-info';
  if (score >= 30) return 'text-signal';
  return 'text-bad';
}

export function timeAgo(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
