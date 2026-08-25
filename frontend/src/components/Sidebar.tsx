'use client';

// Sidebar — the console's instrument rail. Holds navigation, the hunt
// trigger, the live next-hunt status, and the user chip. Collapses to an
// icon rail on md screens; on small screens the page renders a mobile
// top bar instead (this component is hidden below md).

import { motion } from 'framer-motion';
import { Crosshair, LayoutDashboard, Radar, Send, User } from 'lucide-react';
import type { Profile } from '@/types';
import { cn } from '@/lib/utils';
import NextHunt, { useNextRunLabel } from './NextHunt';

export type View = 'dashboard' | 'matches' | 'applications' | 'profile';

export const NAV: { id: View; label: string; icon: typeof Radar }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'matches', label: 'Matches', icon: Crosshair },
  { id: 'applications', label: 'Applications', icon: Send },
  { id: 'profile', label: 'Profile', icon: User },
];

export default function Sidebar({
  view,
  onNavigate,
  pendingCount,
  nextRunAt,
  schedulerEnabled,
  intervalMinutes,
  profile,
  onEditSetup,
  children,
}: {
  view: View;
  onNavigate: (v: View) => void;
  pendingCount: number;
  nextRunAt: string | null;
  schedulerEnabled: boolean;
  intervalMinutes: number;
  profile: Profile | null;
  onEditSetup: () => void;
  children?: React.ReactNode; // slot under the nav (e.g. hunt trigger)
}) {
  const initials =
    (profile?.full_name ?? '')
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase())
      .join('') || null;

  return (
    <aside className="sticky top-0 hidden h-dvh w-16 shrink-0 flex-col border-r border-line bg-surface/60 px-3 py-5 md:flex lg:w-60 lg:px-4">
      {/* Brand */}
      <div className="mb-8 flex items-center gap-3 px-1 lg:px-2">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
          <Radar className="h-5 w-5 text-signal" aria-hidden />
        </div>
        <div className="hidden min-w-0 lg:block">
          <p className="truncate font-semibold tracking-tight text-hi">JobFinderOS</p>
          <p className="num text-[10px] uppercase tracking-widest text-low">TalentHive engine</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex flex-col gap-1" aria-label="Main">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = view === id;
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                active ? 'text-signal' : 'text-mid hover:bg-surface-2 hover:text-hi'
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-indicator"
                  className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-signal"
                  transition={{ type: 'spring', stiffness: 100, damping: 20 }}
                />
              )}
              <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden />
              <span className="hidden lg:inline">{label}</span>
              {id === 'matches' && pendingCount > 0 && (
                <span className="num ml-auto hidden rounded-full bg-signal/15 px-1.5 py-0.5 text-[11px] text-signal lg:inline">
                  {pendingCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Slot (hunt trigger) */}
      {children && <div className="mt-5">{children}</div>}

      <div className="mt-auto space-y-3">
        {/* Live hunt status */}
        <div className="rounded-lg border border-line bg-ink/60 px-3 py-2.5">
          <p className="hidden text-[10px] font-medium uppercase tracking-widest text-mid lg:block">
            Hunt cycle
          </p>
          <NextHunt
            nextRunAt={nextRunAt}
            schedulerEnabled={schedulerEnabled}
            intervalMinutes={intervalMinutes}
          />
        </div>

        {/* User chip */}
        <button
          onClick={onEditSetup}
          className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-surface-2"
          title="Edit your search setup"
        >
          <span className="num flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-surface-2 text-xs text-mid">
            {initials ?? <User className="h-4 w-4" aria-hidden />}
          </span>
          <span className="hidden min-w-0 lg:block">
            <span className="block truncate text-sm font-medium text-hi">
              {profile?.full_name ?? 'Your profile'}
            </span>
            <span className="block truncate text-xs text-low">
              {profile
                ? [profile.municipality ?? profile.region, profile.country === 'SE' ? 'Sweden' : profile.country === 'GB' ? 'United Kingdom' : null]
                    .filter(Boolean)
                    .join(' · ') || 'Set your area'
                : 'Not set up'}
            </span>
          </span>
        </button>
      </div>
    </aside>
  );
}

// Compact label used by the mobile top bar ("next hunt in 1h 22m")
export function NextHuntLabel({
  nextRunAt,
  schedulerEnabled,
  intervalMinutes,
}: {
  nextRunAt: string | null;
  schedulerEnabled: boolean;
  intervalMinutes: number;
}) {
  const label = useNextRunLabel(nextRunAt, schedulerEnabled, intervalMinutes);
  return <span className="num text-xs text-low">{label}</span>;
}
