'use client';

// The localized landing. One component, two languages: the route
// (src/app/page.tsx = EN, src/app/sv/page.tsx = SV) passes the locale
// and every word comes from the dictionary, so the page itself is never
// duplicated. Links stay inside the visitor's language scope where a
// translated page exists (/sv/login); /app and /privacy are English
// surfaces for beta and linked the same from both.

import { useEffect } from 'react';
import Link from 'next/link';
import { ArrowDown, Radar, ShieldCheck } from 'lucide-react';
import GuardReceipt from '@/components/landing/GuardReceipt';
import RadarScope from '@/components/landing/RadarScope';
import Reveal from '@/components/landing/Reveal';
import { dicts, type Locale } from '@/i18n/dict';
import { shouldRedirectToSv, switchLocale } from '@/i18n/locale';

// The landing as three cinematic acts on one material — the console's ink.
//   I. The statement, and the hunt as a giant cropped scope.
//  II. The product, held still: the real hunt pulse beside the pipeline,
//      the real verdict wide, then the sources gallery.
// III. The fact guard receipt, the terms, and one way in.
// Amber appears only on ink. Display type is weight 600, tracking
// tightens as it grows. Motion is critically damped and dies under
// prefers-reduced-motion.

export default function LandingView({ locale }: { locale: Locale }) {
  const t = dicts[locale];

  // First-visit detection (EN routes only): a browser set to Swedish
  // with no stored preference lands on /sv. /sv itself never redirects —
  // the URL is an explicit choice.
  useEffect(() => {
    document.documentElement.lang = locale;
    if (locale === 'en' && window.location.pathname === '/' && shouldRedirectToSv()) {
      window.location.replace('/sv');
    }
  }, [locale]);

  const loginPath = locale === 'sv' ? '/sv/login' : '/login';
  const registerPath = locale === 'sv' ? '/sv/login?mode=register' : '/login?mode=register';
  const otherLocale: Locale = locale === 'en' ? 'sv' : 'en';

  return (
    <div className="bg-ink text-paper">
      {/* Frosted chrome: content scrolls under it, edge fades instead of a rule. */}
      <header className="fixed inset-x-0 top-0 z-50 bg-ink/55 backdrop-blur-xl backdrop-saturate-150">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5" aria-label="JobFinderOS home">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
              <Radar className="h-3.5 w-3.5 text-signal" aria-hidden />
            </span>
            <span className="font-display text-[17px] font-semibold tracking-tight">
              JobFinderOS
            </span>
          </Link>
          <nav
            className="hidden items-center gap-7 text-sm text-paper/60 sm:flex"
            aria-label={t.nav.sections}
          >
            <a href="#how" className="transition hover:text-paper">
              {t.nav.how}
            </a>
            <a href="#sources" className="transition hover:text-paper">
              {t.nav.sources}
            </a>
            <a href="#guard" className="transition hover:text-paper">
              {t.nav.guard}
            </a>
          </nav>
          <div className="flex items-center gap-4">
            <Link
              href={loginPath}
              className="hidden text-sm font-medium text-paper/60 transition hover:text-paper sm:block"
            >
              {t.nav.signIn}
            </Link>
            <Link
              href={registerPath}
              className="rounded-full bg-signal px-4 py-1.5 text-sm font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
            >
              {t.nav.getStarted}
            </Link>
          </div>
        </div>
        <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </header>

      <main>
        {/* ── ACT I · the promise, the proof, the product ──────────── */}
        <section className="relative overflow-hidden bg-ink">
          <RadarScope
            chipRole={t.radar.chipRole}
            matchWord={t.radar.match}
            titles={t.radar.titles}
          />

          <div className="relative z-10 mx-auto w-full max-w-5xl px-6 pt-[12svh] text-center">
            <p className="landing-rise num flex items-center justify-center gap-2.5 text-[11px] uppercase tracking-[0.18em] text-paper/50">
              <span className="relative flex h-2 w-2" aria-hidden>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
              </span>
              {t.hero.badge}
            </p>
            <h1 className="landing-rise landing-rise-1 mt-6 font-display text-[clamp(2.6rem,6vw,4.5rem)] font-semibold leading-[1.04] tracking-[-0.025em] text-paper">
              {t.hero.h1a}
              <br />
              <em className="italic">{t.hero.h1b}</em>
            </h1>
            <p className="landing-rise landing-rise-2 mx-auto mt-6 max-w-lg text-lg leading-relaxed text-paper/60">
              {t.hero.sub}
            </p>
            <div className="landing-rise landing-rise-3 mt-9 flex flex-wrap items-center justify-center gap-x-7 gap-y-4">
              <Link
                href="/app"
                className="rounded-full bg-signal px-7 py-3 text-[15px] font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
              >
                {t.hero.cta}
              </Link>
              <a
                href="#how"
                className="group inline-flex items-center gap-1.5 text-[15px] font-medium text-paper/60 transition hover:text-paper"
              >
                {t.hero.how}
                <ArrowDown
                  className="h-3.5 w-3.5 transition-transform group-hover:translate-y-0.5"
                  aria-hidden
                />
              </a>
            </div>
            {/* Proof beside the CTA — the category pattern, with only
                claims we can stand behind. */}
            <p className="landing-rise landing-rise-3 num mt-6 text-[11px] uppercase tracking-[0.16em] text-paper/50">
              {t.hero.proof}
            </p>
          </div>

          {/* The product, rising out of the fold — viewport one, not a
              scroll away. Melts into the panel below. */}
          <div className="landing-rise landing-rise-3 relative z-10 mx-auto mt-20 max-w-3xl px-6">
            <figure className="overflow-hidden rounded-t-2xl border border-b-0 border-line shadow-[0_-24px_80px_-32px_rgba(0,0,0,0.8)] [mask-image:linear-gradient(to_bottom,black_55%,transparent_97%)]">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/screenshots/hunt-pulse.png"
                alt={t.hero.pulseAlt}
                className="block w-full"
                width={786}
                height={1226}
              />
            </figure>
          </div>

          {/* Deterministic seam: whatever the mask left, ink reclaims it
              before the Act II panel rises over this edge. */}
          <div
            className="pointer-events-none absolute inset-x-0 bottom-0 z-20 h-48 bg-gradient-to-t from-ink via-ink/70 to-transparent"
            aria-hidden
          />
        </section>

        {/* ── ACT II · the product, held still ────────────────────── */}
        <section
          id="how"
          className="relative z-10 -mt-8 scroll-mt-24 rounded-t-[2.5rem] border-t border-line bg-surface shadow-[0_-24px_60px_-24px_rgba(0,0,0,0.6)]"
        >
          {/* The numbers, up front — the category's stats band, with
              only counts we can defend. */}
          <div className="border-b border-line">
            <div className="mx-auto grid max-w-6xl grid-cols-2 gap-x-6 gap-y-10 px-6 pb-14 pt-20 sm:grid-cols-4 sm:pt-24">
              {t.stats.map(([value, label]) => (
                <Reveal key={label}>
                  <p className="num font-display text-4xl font-semibold tracking-tight text-paper sm:text-[2.75rem]">
                    {value}
                  </p>
                  <p className="num mt-2 text-[10px] uppercase tracking-[0.16em] text-paper/45">
                    {label}
                  </p>
                </Reveal>
              ))}
            </div>
          </div>

          <div className="mx-auto max-w-6xl px-6 pb-28 pt-16 sm:pt-20">
            <div className="grid gap-16 lg:grid-cols-[0.9fr_1.1fr] lg:gap-20">
              <div className="lg:sticky lg:top-28 lg:self-start">
                <Reveal>
                  <figure className="overflow-hidden rounded-2xl border border-line shadow-[0_24px_60px_-24px_rgba(0,0,0,0.7)]">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src="/screenshots/hunt-pulse.png"
                      alt={t.hero.pulseAlt}
                      className="block w-full"
                      width={786}
                      height={1226}
                    />
                  </figure>
                  <p className="num mt-3 text-[10px] uppercase tracking-[0.16em] text-paper/50">
                    {t.verdict.pulseCaption}
                  </p>
                </Reveal>
              </div>

              <div>
                <Reveal>
                  <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
                    {t.verdict.h2a}
                    <br />
                    {t.verdict.h2b}
                  </h2>
                </Reveal>
                <ol className="mt-12">
                  {t.steps.map((step, i) => (
                    <Reveal key={step.title} delay={i * 90}>
                      <li className="grid gap-2.5 border-t border-line py-7 sm:grid-cols-[4.5rem_1fr] sm:gap-8">
                        <p className="num pt-1.5 text-sm text-paper/35">
                          {String(i + 1).padStart(2, '0')}
                        </p>
                        <div>
                          <h3 className="font-display text-xl font-semibold tracking-tight text-paper">
                            {step.title}
                          </h3>
                          <p className="mt-2 max-w-md leading-relaxed text-paper/70">
                            {step.body}
                          </p>
                        </div>
                      </li>
                    </Reveal>
                  ))}
                </ol>
              </div>
            </div>
          </div>

          {/* The verdict, wide: what you have, what's missing, what transfers. */}
          <div className="border-t border-line">
            <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
                  {t.verdict.h2}
                </h2>
                <p className="mt-5 max-w-2xl text-lg leading-relaxed text-paper/70">
                  {t.verdict.body}
                </p>
              </Reveal>
              <Reveal delay={120}>
                <figure className="mt-12 overflow-hidden rounded-2xl border border-line shadow-[0_24px_60px_-24px_rgba(0,0,0,0.7)]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src="/screenshots/match-detail.png"
                    alt={t.verdict.alt}
                    className="block w-full"
                    width={776}
                    height={402}
                  />
                </figure>
                <p className="num mt-4 text-[10px] uppercase tracking-[0.16em] text-paper/40">
                  {t.verdict.caption}
                </p>
              </Reveal>
            </div>
          </div>

          {/* The anti-volume argument: the market data, inverted. */}
          <div className="border-t border-line">
            <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
                  {t.volume.h2}
                </h2>
                <p className="mt-5 max-w-2xl text-lg leading-relaxed text-paper/70">
                  {t.volume.body}
                </p>
                <p className="num mt-8 text-xs uppercase tracking-[0.14em] text-paper/45">
                  {t.volume.stat}
                </p>
              </Reveal>
            </div>
          </div>

          {/* Sources: momentum gallery, snap-aligned. Named sites stay out
              of the copy; the framing is coverage and precision instead. */}
          <div id="sources" className="scroll-mt-24 border-t border-line bg-surface-2/50">
            <div className="mx-auto max-w-6xl px-6 py-24">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
                  {t.sourcesSec.h2}
                </h2>
              </Reveal>
              <Reveal delay={90}>
                <div
                  role="region"
                  aria-label={t.sourcesSec.ariaLabel}
                  tabIndex={0}
                  className="no-scrollbar -mx-6 mt-10 flex snap-x snap-mandatory gap-5 overflow-x-auto px-6 pb-2"
                >
                  {t.sourcesSec.cards.map((s) => (
                    <div
                      key={s.title}
                      className="min-w-[85%] snap-start rounded-2xl border border-line bg-surface p-8 sm:min-w-[340px]"
                    >
                      <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/45">
                        {s.tag}
                      </p>
                      <h3 className="mt-4 font-display text-2xl font-semibold tracking-tight text-paper">
                        {s.title}
                      </h3>
                      <p className="mt-3 text-sm leading-relaxed text-paper/60">{s.body}</p>
                    </div>
                  ))}
                </div>
              </Reveal>
              <Reveal delay={150}>
                {/* OPS-6: the old line here claimed "Your data stays in the
                    EU" — false, and exactly what the privacy work corrects:
                    stored account data is EU-hosted (Supabase eu-west-1,
                    Render Frankfurt), but CV text is sent to Z.ai outside
                    the EU on every match/tailor/judge call. This line now
                    says only what is true, and links the full notice. */}
                <p className="mt-10 text-sm leading-relaxed text-paper/50">
                  {t.sourcesSec.privacyA}{' '}
                  <Link
                    href="/privacy"
                    className="font-medium text-paper/80 underline underline-offset-4 transition hover:text-paper"
                  >
                    {t.sourcesSec.privacyLink}
                  </Link>
                  .
                </p>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── ACT III · ink close: the guard, and one way in ──────── */}
        <section
          id="guard"
          className="relative z-10 -mt-8 scroll-mt-24 rounded-t-[2.5rem] border-t border-line bg-ink shadow-[0_-24px_60px_-24px_rgba(0,0,0,0.6)]"
        >
          <div className="mx-auto max-w-6xl px-6 py-28">
            <div className="grid items-center gap-14 lg:grid-cols-2 lg:gap-20">
              <Reveal>
                <ShieldCheck className="h-8 w-8 text-ok" aria-hidden />
                <h2 className="mt-6 font-display text-[clamp(2.25rem,4.5vw,3.5rem)] font-semibold leading-[1.06] tracking-[-0.02em] text-paper">
                  {t.guardSec.h2a}
                  <br />
                  {t.guardSec.h2b}
                </h2>
                <p className="mt-5 max-w-md text-lg leading-relaxed text-paper/60">
                  {t.guardSec.body}
                </p>
              </Reveal>
              <Reveal delay={120}>
                <GuardReceipt strings={t.guardSec.receipt} />
              </Reveal>
            </div>
          </div>

          {/* Billing complaints, inverted into terms. In beta there is no
              billing at all; the status line keeps that honest. */}
          <div className="border-t border-line">
            <div className="mx-auto grid max-w-6xl gap-12 px-6 py-20 lg:grid-cols-2 lg:gap-20">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
                  {t.terms.h2a}
                  <br />
                  {t.terms.h2b}
                </h2>
                <p className="num mt-6 max-w-xs text-xs uppercase leading-relaxed tracking-[0.14em] text-paper/45">
                  {t.terms.note}
                </p>
              </Reveal>
              <Reveal delay={120}>
                <ul className="divide-y divide-line text-[17px] leading-relaxed text-paper/80">
                  {t.terms.items.map((item) => (
                    <li key={item} className="py-4">
                      {item}
                    </li>
                  ))}
                </ul>
              </Reveal>
            </div>
          </div>

          <div className="border-t border-line">
            <div className="mx-auto max-w-6xl px-6 py-28 text-center">
              <Reveal>
                <h2 className="font-display text-[clamp(2.5rem,5.5vw,4.5rem)] font-semibold leading-[1.05] tracking-[-0.022em] text-paper">
                  {t.final.h2a}
                  <br />
                  {t.final.h2b}
                </h2>
                <p className="mx-auto mt-5 text-lg text-paper/55">{t.final.sub}</p>
                <Link
                  href="/app"
                  className="mt-10 inline-block rounded-full bg-signal px-8 py-3.5 text-[15px] font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
                >
                  {t.final.cta}
                </Link>
              </Reveal>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line bg-ink">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-8 sm:flex-row sm:items-center sm:justify-between">
          <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/40">
            JobFinderOS
          </p>
          <div className="num flex items-center gap-5 text-[10px] uppercase tracking-[0.16em] text-paper/40">
            {/* OPS-6: footer privacy link replaces the old imprecise "EU
                hosted" tagline (stored data is EU-hosted, but CV text
                reaches Z.ai outside the EU — the /privacy notice says both). */}
            <Link
              href="/privacy"
              className="transition hover:text-paper/70"
            >
              {t.footer.privacy}
            </Link>
            {/* Language toggle: stores the choice, then swaps the scoped
                path. Explicit choice beats auto-detection forever. */}
            <button
              type="button"
              onClick={() => {
                window.location.href = switchLocale(otherLocale, '/');
              }}
              className="transition hover:text-paper/70"
            >
              {t.langToggle}
            </button>
          </div>
          <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/40">
            {t.footer.made}
          </p>
        </div>
      </footer>
    </div>
  );
}
