import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowDown, ChevronDown, Radar, ShieldCheck } from 'lucide-react';
import CockpitPreview from '@/components/landing/CockpitPreview';
import GuardReceipt from '@/components/landing/GuardReceipt';
import RadarScope from '@/components/landing/RadarScope';
import Reveal from '@/components/landing/Reveal';

// The landing as three cinematic acts, not a section conveyor:
//   I. Ink opening: the statement, and the hunt as a giant cropped scope.
//  II. Daylight: the product held still (sticky card + pipeline), then
//      the sources gallery, snap-scrolling with momentum.
// III. Ink close: the fact guard receipt, and one way in.
// Amber appears only on ink. Display type is weight 600, tracking
// tightens as it grows. Motion is critically damped and dies under
// prefers-reduced-motion.

export const metadata: Metadata = {
  title: 'JobFinderOS · Stop refreshing job boards',
  description:
    'Hourly hunts across Platsbanken and Reed, every ad scored against your CV. Applications you approve, drafts that never invent facts.',
};

const steps = [
  {
    title: 'Upload once',
    body: 'Your CV, your municipalities, your minimum score. Malmö and Lund, or all of Skåne.',
  },
  {
    title: 'The hunt runs hourly',
    body: 'New ads scraped, agency cross-posts merged into the direct ad, every survivor scored. The rest never reach you.',
  },
  {
    title: 'Approve, tailor, send',
    body: 'One click and GLM writes the CV and cover letter shaped to the ad. The guard rejects anything your history cannot back.',
  },
];

const sources = [
  {
    tag: 'Sweden · JobTech API',
    title: 'Platsbanken, all of it.',
    body: 'Every public job in Sweden, through the official JobTech API.',
  },
  {
    tag: 'United Kingdom · Reed API',
    title: 'Reed.co.uk',
    body: 'The UK job market, every sector, every day, scored by the same engine.',
  },
  {
    tag: 'Scope · taxonomy precise',
    title: 'Your municipalities',
    body: 'Filtered with JobTech taxonomy codes, not fuzzy text matching.',
  },
];

export default function LandingPage() {
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
            aria-label="Landing sections"
          >
            <a href="#how" className="transition hover:text-paper">
              How it works
            </a>
            <a href="#sources" className="transition hover:text-paper">
              Sources
            </a>
            <a href="#guard" className="transition hover:text-paper">
              Fact guard
            </a>
          </nav>
          <Link
            href="/login"
            className="rounded-full border border-paper/20 px-4 py-1.5 text-sm font-medium text-paper transition hover:border-paper/50 active:scale-[0.97]"
          >
            Sign in
          </Link>
        </div>
        <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </header>

      <main>
        {/* ── ACT I · the statement and the scope ─────────────────── */}
        <section className="relative flex min-h-[100svh] flex-col overflow-hidden bg-ink">
          <RadarScope />

          <div className="relative z-10 mx-auto w-full max-w-5xl px-6 pt-[16svh] text-center">
            <p className="landing-rise num flex items-center justify-center gap-2.5 text-[11px] uppercase tracking-[0.18em] text-paper/50">
              <span className="relative flex h-2 w-2" aria-hidden>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
              </span>
              Sweden + UK · hourly hunts
            </p>
            <h1 className="landing-rise landing-rise-1 mt-6 font-display text-[clamp(3rem,8vw,6.25rem)] font-semibold leading-[1.03] tracking-[-0.025em] text-paper">
              Stop refreshing <em className="italic">job boards.</em>
            </h1>
            <p className="landing-rise landing-rise-2 mx-auto mt-6 max-w-md text-lg leading-relaxed text-paper/60">
              Platsbanken and Reed, hunted hourly and scored against your CV.
            </p>
            <div className="landing-rise landing-rise-3 mt-9 flex flex-wrap items-center justify-center gap-x-7 gap-y-4">
              <Link
                href="/app"
                className="rounded-full bg-signal px-7 py-3 text-[15px] font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
              >
                Upload your CV
              </Link>
              <a
                href="#how"
                className="group inline-flex items-center gap-1.5 text-[15px] font-medium text-paper/60 transition hover:text-paper"
              >
                See how it works
                <ArrowDown
                  className="h-3.5 w-3.5 transition-transform group-hover:translate-y-0.5"
                  aria-hidden
                />
              </a>
            </div>
            <p className="landing-rise landing-rise-3 num mt-10 text-[10px] uppercase tracking-[0.18em] text-paper/35">
              Next hunt on the hour
            </p>
          </div>

          <div className="absolute bottom-12 left-1/2 z-10 -translate-x-1/2" aria-hidden>
            <ChevronDown className="h-5 w-5 animate-bounce text-paper/50" />
          </div>
        </section>

        {/* ── ACT II · daylight: the product, held still ──────────── */}
        <section id="how" className="relative z-10 -mt-8 scroll-mt-24 rounded-t-[2.5rem] bg-paper text-ink shadow-[0_-24px_60px_-24px_rgba(12,14,18,0.45)]">
          <div className="mx-auto max-w-6xl px-6 pb-28 pt-24 sm:pt-28">
            <div className="grid gap-16 lg:grid-cols-[0.95fr_1.05fr] lg:gap-20">
              <div className="lg:sticky lg:top-28 lg:self-start">
                <Reveal>
                  <CockpitPreview />
                  <p className="num mt-4 text-[10px] uppercase tracking-[0.16em] text-ink/40">
                    A real match, as it lands in the console
                  </p>
                </Reveal>
              </div>

              <div>
                <Reveal>
                  <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em]">
                    It hunts while
                    <br />
                    you do anything else.
                  </h2>
                </Reveal>
                <ol className="mt-12">
                  {steps.map((step, i) => (
                    <Reveal key={step.title} delay={i * 90}>
                      <li className="grid gap-2.5 border-t border-paper-line py-7 sm:grid-cols-[4.5rem_1fr] sm:gap-8">
                        <p className="num pt-1.5 text-sm text-ink/35">
                          {String(i + 1).padStart(2, '0')}
                        </p>
                        <div>
                          <h3 className="font-display text-xl font-semibold tracking-tight">
                            {step.title}
                          </h3>
                          <p className="mt-2 max-w-md leading-relaxed text-ink/65">
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

          {/* The anti-volume argument: the market data, inverted. */}
          <div className="border-t border-paper-line">
            <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em]">
                  Volume is not a strategy.
                </h2>
                <p className="mt-5 max-w-2xl text-lg leading-relaxed text-ink/65">
                  The mass-apply era made recruiters numb: two thirds of hiring
                  managers say AI-written CVs make your skills harder to
                  verify. More applications, less signal. We send fewer
                  matches, and we tell you when a job is not worth applying
                  for.
                </p>
                <p className="num mt-8 text-xs uppercase tracking-[0.14em] text-ink/45">
                  65% of hiring managers say AI-optimized CVs make skills
                  harder to verify. Forbes, March 2026.
                </p>
              </Reveal>
            </div>
          </div>

          {/* Sources: momentum gallery, snap-aligned. */}
          <div id="sources" className="scroll-mt-24 border-t border-paper-line bg-paper-deep">
            <div className="mx-auto max-w-6xl px-6 py-24">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em]">
                  Where we hunt.
                </h2>
              </Reveal>
              <Reveal delay={90}>
                <div
                  role="region"
                  aria-label="Job sources"
                  tabIndex={0}
                  className="no-scrollbar -mx-6 mt-10 flex snap-x snap-mandatory gap-5 overflow-x-auto px-6 pb-2"
                >
                  {sources.map((s) => (
                    <div
                      key={s.title}
                      className="min-w-[85%] snap-start rounded-2xl border border-paper-line bg-paper p-8 sm:min-w-[340px]"
                    >
                      <p className="num text-[10px] uppercase tracking-[0.16em] text-ink/45">
                        {s.tag}
                      </p>
                      <h3 className="mt-4 font-display text-2xl font-semibold tracking-tight">
                        {s.title}
                      </h3>
                      <p className="mt-3 text-sm leading-relaxed text-ink/60">{s.body}</p>
                    </div>
                  ))}
                </div>
              </Reveal>
              <Reveal delay={150}>
                <p className="num mt-10 text-xs uppercase tracking-[0.14em] text-ink/45">
                  Public APIs only. No logins, no grey scraping. Your data
                  stays in the EU.
                </p>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── ACT III · ink close: the guard, and one way in ──────── */}
        <section id="guard" className="scroll-mt-24 bg-ink">
          <div className="mx-auto max-w-6xl px-6 py-28">
            <div className="grid items-center gap-14 lg:grid-cols-2 lg:gap-20">
              <Reveal>
                <ShieldCheck className="h-8 w-8 text-ok" aria-hidden />
                <h2 className="mt-6 font-display text-[clamp(2.25rem,4.5vw,3.5rem)] font-semibold leading-[1.06] tracking-[-0.02em] text-paper">
                  Nothing invented.
                  <br />
                  Ever.
                </h2>
                <p className="mt-5 max-w-md text-lg leading-relaxed text-paper/60">
                  Every claim in your tailored CV is checked against your real
                  CV before it&apos;s sent. If a claim is not in your history,
                  it does not ship.
                </p>
              </Reveal>
              <Reveal delay={120}>
                <GuardReceipt />
              </Reveal>
            </div>
          </div>

          {/* Billing complaints, inverted into terms. In beta there is no
              billing at all; the status line keeps that honest. */}
          <div className="border-t border-line">
            <div className="mx-auto grid max-w-6xl gap-12 px-6 py-20 lg:grid-cols-2 lg:gap-20">
              <Reveal>
                <h2 className="font-display text-[clamp(2rem,4vw,3rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-paper">
                  No credits.
                  <br />
                  No surprises.
                </h2>
                <p className="num mt-6 max-w-xs text-xs uppercase leading-relaxed tracking-[0.14em] text-paper/45">
                  In beta it is free. Whenever we charge, these are the terms.
                </p>
              </Reveal>
              <Reveal delay={120}>
                <ul className="divide-y divide-line text-[17px] leading-relaxed text-paper/80">
                  <li className="py-4">
                    No credits and no surprise charges. The price is the price.
                  </li>
                  <li className="py-4">The trial never auto-renews.</li>
                  <li className="py-4">Cancel in one click.</li>
                  <li className="py-4">
                    Get hired, and we refund the months you have not used.
                  </li>
                </ul>
              </Reveal>
            </div>
          </div>

          <div className="border-t border-line">
            <div className="mx-auto max-w-6xl px-6 py-28 text-center">
              <Reveal>
                <h2 className="font-display text-[clamp(2.5rem,5.5vw,4.5rem)] font-semibold leading-[1.05] tracking-[-0.022em] text-paper">
                  Your next job is
                  <br />
                  already posted.
                </h2>
                <p className="mx-auto mt-5 text-lg text-paper/55">
                  Somewhere in tonight&apos;s hunt.
                </p>
                <Link
                  href="/app"
                  className="mt-10 inline-block rounded-full bg-signal px-8 py-3.5 text-[15px] font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.97]"
                >
                  Upload your CV
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
          <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/40">
            Data: JobTech (Platsbanken) · Reed.co.uk
          </p>
          <p className="num text-[10px] uppercase tracking-[0.16em] text-paper/40">
            Made in Malmö
          </p>
        </div>
      </footer>
    </div>
  );
}
