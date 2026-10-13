import Link from 'next/link';
import { ArrowLeft, Radar } from 'lucide-react';
import { dicts, type Locale } from '@/i18n/dict';
import type { PrivacyBlock } from '@/i18n/privacy';
import { rich } from '@/i18n/rich';

// OPS-6: the privacy notice, rendered from the dictionary (i18n/privacy.ts
// — the factual-accuracy rule and every claim's source trace live there).
// A server component: pure markup, ships as static HTML in the Cloudflare
// Pages export at /privacy (EN) and /sv/privacy (SV).

function Section({
  n,
  title,
  children,
}: {
  n: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-line py-8 first:border-t-0 first:pt-0">
      <h2 className="flex items-baseline gap-3 font-display text-xl font-semibold tracking-tight text-hi">
        <span className="num text-sm text-signal">{n}</span>
        {title}
      </h2>
      <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-mid">{children}</div>
    </section>
  );
}

function Block({ block }: { block: PrivacyBlock }) {
  if (block.kind === 'p') {
    return <p className={block.muted ? 'text-sm text-low' : undefined}>{rich(block.text)}</p>;
  }
  if (block.kind === 'list') {
    return (
      <ul className="list-disc space-y-1.5 pl-5">
        {block.items.map((item, i) => (
          <li key={i}>{rich(item)}</li>
        ))}
      </ul>
    );
  }
  return (
    <>
      <p>{block.intro}</p>
      <ul className="space-y-2.5">
        {block.items.map((item, i) => (
          <li key={i} className="rounded-lg border border-line bg-surface/60 p-3">
            <p className="text-sm font-medium text-hi">
              {item.name}
              {item.where && (
                <span className={`font-normal ${item.tone === 'ok' ? 'text-ok' : 'text-signal'}`}>
                  {' '}
                  · {item.where}
                </span>
              )}
            </p>
            <p className="mt-1 text-sm">{rich(item.body)}</p>
            {item.note && <p className="mt-1.5 text-xs leading-relaxed text-low">{item.note}</p>}
          </li>
        ))}
      </ul>
    </>
  );
}

export default function PrivacyView({ locale }: { locale: Locale }) {
  const t = dicts[locale].privacy;
  return (
    <div lang={locale} className="console-backdrop min-h-dvh bg-ink text-hi">
      <main className="mx-auto max-w-2xl px-6 py-14">
        <Link
          href={locale === 'sv' ? '/sv' : '/'}
          className="mb-2 inline-flex items-center gap-1.5 text-sm text-low transition hover:text-hi"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t.backHome}
        </Link>

        <div className="mb-10 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
            <Radar className="h-5 w-5 text-signal" aria-hidden />
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">{t.title}</h1>
            <p className="num text-[10px] uppercase tracking-widest text-low">{t.stamp}</p>
          </div>
          <Link
            href={locale === 'sv' ? '/privacy' : '/sv/privacy'}
            hrefLang={locale === 'sv' ? 'en' : 'sv'}
            className="ml-auto text-xs text-low underline underline-offset-4 transition hover:text-hi"
          >
            {dicts[locale].langToggle}
          </Link>
        </div>

        <p className="rounded-xl border border-line bg-surface/60 p-4 text-sm leading-relaxed text-mid">
          {rich(t.intro)}
        </p>

        <div className="mt-8">
          {t.sections.map((s) => (
            <Section key={s.n} n={s.n} title={s.title}>
              {s.blocks.map((b, i) => (
                <Block key={i} block={b} />
              ))}
            </Section>
          ))}
        </div>

        <p className="mt-10 border-t border-line pt-6 text-xs leading-relaxed text-low">{t.footer}</p>
      </main>
    </div>
  );
}
