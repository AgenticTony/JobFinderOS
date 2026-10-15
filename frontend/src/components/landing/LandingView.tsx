'use client';

// The localized landing. One component, two languages: the route
// (src/app/page.tsx = EN, src/app/sv/page.tsx = SV) passes the locale
// and every word comes from the dictionary, so the page itself is never
// duplicated. Links stay inside the visitor's language scope where a
// translated page exists (/sv/login, /sv/privacy); /app has no /sv
// route — the console reads the saved language (i18n/locale.ts).
//
// WO-28: the sections below Act I live in components/landing/ so WO-29's
// persona pages compose the same pieces instead of copying them. The
// landing owns the page order and the acts; the components own their
// slice of the dictionary.

import { useEffect } from 'react';
import Link from 'next/link';
import { ArrowDown, Radar } from 'lucide-react';
import ComparisonTable from '@/components/landing/ComparisonTable';
import Faq from '@/components/landing/Faq';
import FinalCta from '@/components/landing/FinalCta';
import GuardSection from '@/components/landing/GuardSection';
import HuntDemo from '@/components/landing/HuntDemo';
import ProofSlot from '@/components/landing/ProofSlot';
import RadarScope from '@/components/landing/RadarScope';
import SourcesGallery from '@/components/landing/SourcesGallery';
import StatsBand from '@/components/landing/StatsBand';
import Steps from '@/components/landing/Steps';
import StickyCta from '@/components/landing/StickyCta';
import TermsSection from '@/components/landing/TermsSection';
import VerdictWide from '@/components/landing/VerdictWide';
import VolumeArgument from '@/components/landing/VolumeArgument';
import { dicts, type Locale } from '@/i18n/dict';
import { shouldRedirectToSv, switchLocale } from '@/i18n/locale';

// The landing as three cinematic acts on one material — the console's ink.
//   I. The statement, and the hunt as a giant cropped scope.
//  II. The product, held still: the pipeline and the verdict, then the
//      demo that makes the verdict interactive, the anti-volume argument,
//      the comparison, and the sources gallery.
// III. The fact guard receipt, the honest proof slot, the terms, the
//      FAQ, and one way in.
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
  const privacyPath = locale === 'sv' ? '/sv/privacy' : '/privacy';
  const registerPath = locale === 'sv' ? '/sv/login?mode=register' : '/login?mode=register';
  const otherLocale: Locale = locale === 'en' ? 'sv' : 'en';

  return (
    <div className="bg-ink text-paper">
      {/* Frosted chrome: content scrolls under it, edge fades instead of a rule. */}
      <header className="fixed inset-x-0 top-0 z-50 bg-ink/55 backdrop-blur-xl backdrop-saturate-150">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5" aria-label={t.nav.home}>
            <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-signal/30 bg-signal/10">
              <Radar className="h-3.5 w-3.5 text-signal" aria-hidden />
            </span>
            <span className="font-display text-[17px] font-semibold tracking-tight">
              JobFinderOS
            </span>
          </Link>
          <nav
            className="hidden items-center gap-7 text-sm text-paper/60 lg:flex"
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
            <a href="#faq" className="transition hover:text-paper">
              {t.nav.faq}
            </a>
          </nav>
          <div className="flex items-center gap-4">
            {/* Language toggle in the header, not only the footer: the
                auto-redirect covers sv browsers, but a Swede on an
                en-locale browser (or via a shared link) meets an all-
                English page — "Svenska" must be visible above the fold. */}
            <button
              type="button"
              onClick={() => switchLocale(otherLocale, '/')}
              lang={otherLocale}
              className="text-sm font-medium text-paper/60 transition hover:text-paper"
            >
              {t.langToggle}
            </button>
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

          {/* pt floor: the fixed h-14 header is 56px tall — 12svh is
              less than that on short/landscape viewports (e.g. 667px
              landscape → 44px), which put the badge under the header.
              max() keeps the fold clear at every height. */}
          <div className="relative z-10 mx-auto w-full max-w-5xl px-6 pt-[max(12svh,5.5rem)] text-center">
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
              <em className="italic text-signal">{t.hero.h1b}</em>
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
                claims we can stand behind. The formats line states the
                real upload contract (5 MB, PDF/Word — file_service.py). */}
            <p className="landing-rise landing-rise-3 num mt-6 text-[11px] uppercase tracking-[0.16em] text-paper/50">
              {t.hero.proof}
            </p>
            <p className="landing-rise landing-rise-3 num mt-2 text-[11px] uppercase tracking-[0.16em] text-paper/40">
              {t.hero.formats}
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
          <StatsBand t={t} />
          <Steps t={t} />
          <VerdictWide t={t} />
          <div className="border-t border-line">
            <HuntDemo t={t} />
          </div>
          <div className="border-t border-line">
            <VolumeArgument t={t} />
          </div>
          <div className="border-t border-line">
            <ComparisonTable t={t} />
          </div>
          <div id="sources" className="scroll-mt-24">
            <SourcesGallery t={t} privacyPath={privacyPath} />
          </div>
        </section>

        {/* ── ACT III · ink close: the guard, the proof, one way in ── */}
        <section
          id="guard"
          className="relative z-10 -mt-8 scroll-mt-24 rounded-t-[2.5rem] border-t border-line bg-ink shadow-[0_-24px_60px_-24px_rgba(0,0,0,0.6)]"
        >
          <GuardSection t={t} />
          <div className="border-t border-line">
            <ProofSlot t={t} />
          </div>
          <TermsSection t={t} />
          <div className="border-t border-line">
            <Faq t={t} />
          </div>
          <div className="border-t border-line">
            <FinalCta t={t} />
          </div>
        </section>
      </main>

      <footer className="border-t border-line bg-ink">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-8 sm:flex-row sm:items-center sm:justify-between">
          <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/40">
            JobFinderOS
          </p>
          <div className="num flex items-center gap-5 text-[10px] uppercase tracking-[0.16em] text-paper/40">
            <Link
              href={privacyPath}
              className="transition hover:text-paper/70"
            >
              {t.footer.privacy}
            </Link>
            {/* Language toggle: stores the choice, then swaps the scoped
                path. Explicit choice beats auto-detection forever. */}
            <button
              type="button"
              onClick={() => switchLocale(otherLocale, '/')}
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

      {/* Sticky mobile CTA + the spacer that keeps it off the footer's
          last row (bar height ≈ 56px; spacer on phones only). */}
      <div className="h-14 sm:hidden" aria-hidden />
      <StickyCta t={t} />
    </div>
  );
}
