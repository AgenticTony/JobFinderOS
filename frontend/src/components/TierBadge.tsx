'use client';

import type { Tier } from '@/types';
import { TIER_CONFIG, cn } from '@/lib/utils';
import { useT } from '@/i18n/LocaleContext';

export default function TierBadge({ tier, className }: { tier: Tier; className?: string }) {
  const tiers = useT().console.tiers;
  const known = tier in TIER_CONFIG;
  const config = known ? TIER_CONFIG[tier] : TIER_CONFIG.poor_match;
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
        config.color,
        className
      )}
    >
      {known ? tiers[tier] : tiers.poor_match}
    </span>
  );
}
