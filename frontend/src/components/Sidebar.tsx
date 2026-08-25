'use client';

// Sidebar — the console's instrument rail. Navigation with expandable
// groups (Better Stack / Midday pattern): Matches and Applications each
// own two dedicated pages. Collapses to an icon rail on md screens; on
// small screens the page renders a mobile top bar instead (this
// component is hidden below md).

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, Crosshair, LayoutDashboard, Radar, Send, User } from 'lucide-react';
import type { Profile } from '@/types';
import { cn } from '@/lib/utils';
import NextHunt from './NextHunt';

export type View =
  | 'dashboard'
  | 'matches'
  | 'matches-approved'
  | 'apps-review'
  | 'apps-sent'
  | 'profile';

export const NAV: {
  id: View;
  label: string;
  icon: typeof Radar;
  children?: { id: View; label: string; count?: number }[];
}[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  {
    id: 'matches',
    label: 'Matches',
    icon: Crosshair,
    children: [
      { id: 'matches', label: 'Awaiting you' },
      { id: 'matches-approved', label: 'Approved' },
    ],
  },
  {
    id: 'apps-review',
    label: 'Applications',
    icon: Send,
    children: [
      { id: 'apps-review', label: 'Review & send' },
      { id: 'apps-sent', label: 'Sent' },
    ],
  },
  { id: 'profile', label: 'Profile', icon: User },
];

export default function Sidebar({
  view,
  onNavigate,
  pendingCount,
  reviewCount,
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
  reviewCount: number;
  nextRunAt: string | null;
  schedulerEnabled: boolean;
  intervalMinutes: number;
  profile: Profile | null;
  onEditSetup: () => void;
  children?: React.ReactNode; // slot under the nav (e.g. hunt trigger)
}) {
  // Which group is unfolded; the group owning the active page stays open
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  useEffect(() => {
    const group = NAV.find((n) => n.children?.some((c) => c.id === view));
    setOpenGroup(group ? group.label : null);
  }, [view]);

  const countFor = (id: View) =>
    id === 'matches' ? pendingCount : id === 'apps-review' ? reviewCount : 0;

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
        {NAV.map(({ id, label, icon: Icon, children: navChildren }) => {
          const active =
            view === id || (navChildren?.some((c) => c.id === view) && id !== view) || false;
          const groupActive = Boolean(navChildren?.some((c) => c.id === view));
          const isOpen = openGroup === label;

          return (
            <div key={id}>
              <button
                onClick={() => {
                  if (navChildren) {
                    setOpenGroup(isOpen && !groupActive ? null : label);
                    if (!groupActive || !isOpen) onNavigate(navChildren[0].id);
                  } else {
                    onNavigate(id);
                  }
                }}
                aria-current={active ? 'page' : undefined}
                aria-expanded={navChildren ? isOpen : undefined}
                className={cn(
                  'group relative flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  active ? 'text-signal' : 'text-mid hover:bg-surface-2 hover:text-hi'
                )}
              >
                {active && !navChildren && (
                  <motion.span
                    layoutId="nav-indicator"
                    className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-signal"
                    transition={{ type: 'spring', stiffness: 100, damping: 20 }}
                  />
                )}
                <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden />
                <span className="hidden min-w-0 flex-1 text-left lg:inline">{label}</span>
                {navChildren && (
                  <ChevronDown
                    className={cn(
                      'hidden h-4 w-4 shrink-0 transition-transform lg:block',
                      groupActive && 'text-signal',
                      isOpen && 'rotate-180'
                    )}
                    aria-hidden
                  />
                )}
              </button>

              {/* Sub-pages */}
              {navChildren && isOpen && (
                <div className="mt-0.5 hidden space-y-0.5 lg:block">
                  {navChildren.map((c) => {
                    const childActive = view === c.id;
                    const count = countFor(c.id);
                    return (
                      <button
                        key={c.id}
                        onClick={() => onNavigate(c.id)}
                        aria-current={childActive ? 'page' : undefined}
                        className={cn(
                          'relative flex w-full items-center gap-2.5 rounded-lg py-1.5 pl-10 pr-3 text-[13px] transition-colors',
                          childActive ? 'text-signal' : 'text-low hover:bg-surface-2 hover:text-mid'
                        )}
                      >
                        {childActive && (
                          <span className="absolute left-[22px] top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-signal" />
                        )}
                        <span className="min-w-0 flex-1 text-left">{c.label}</span>
                        {count > 0 && (
                          <span className="num rounded-full bg-signal/15 px-1.5 text-[11px] text-signal">
                            {count}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
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
