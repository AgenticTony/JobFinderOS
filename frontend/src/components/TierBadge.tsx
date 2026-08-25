import type { Tier } from '@/types';
import { TIER_CONFIG, cn } from '@/lib/utils';

export default function TierBadge({ tier, className }: { tier: Tier; className?: string }) {
  const config = TIER_CONFIG[tier] ?? TIER_CONFIG.poor_match;
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
        config.color,
        className
      )}
    >
      {config.label}
    </span>
  );
}
