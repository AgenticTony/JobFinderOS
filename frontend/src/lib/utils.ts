import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Tier } from '@/types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const TIER_CONFIG: Record<Tier, { label: string; color: string; ring: string }> = {
  excellent_match: { label: 'Excellent Match', color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', ring: 'text-emerald-400' },
  good_match: { label: 'Good Match', color: 'bg-sky-500/15 text-sky-400 border-sky-500/30', ring: 'text-sky-400' },
  stretch: { label: 'Stretch', color: 'bg-amber-500/15 text-amber-400 border-amber-500/30', ring: 'text-amber-400' },
  poor_match: { label: 'Poor Match', color: 'bg-rose-500/15 text-rose-400 border-rose-500/30', ring: 'text-rose-400' },
};

export function scoreColor(score: number): string {
  if (score >= 80) return 'text-emerald-400';
  if (score >= 50) return 'text-sky-400';
  if (score >= 30) return 'text-amber-400';
  return 'text-rose-400';
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
