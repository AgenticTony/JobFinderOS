import { cn } from '@/lib/utils';

export default function StatCard({
  label,
  value,
  accent,
  hint,
}: {
  label: string;
  value: number | string;
  accent?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      <p className={cn('mt-1 text-2xl font-semibold text-zinc-100', accent)}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}
