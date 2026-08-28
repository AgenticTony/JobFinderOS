import type { Metadata } from 'next';
import Link from 'next/link';
import { Radar, ShieldCheck } from 'lucide-react';
import CockpitPreview from '@/components/landing/CockpitPreview';

// Landing: the daylight half of JobFinderOS. Bone paper ground, Swedish
// grotesk display, amber reserved for the machine (radar, score). The
// console at /app stays ink. One signature: the cockpit preview.

export const metadata: Metadata = {
  title: 'JobFinderOS · Stop refreshing job boards',
  description:
    'Hourly hunts across Platsbanken and Reed, every ad scored against your CV. Applications you approve, drafts that never invent facts.',
};

const stats = [
  {
    value: 'Every hour',
    body: 'A fresh hunt across your boards. You never touch a refresh button again.',
  },
  {
    value: '0 to 100',
    body: 'Every ad scored against your CV. Weak matches never reach you.',
  },
  {
    value: 'You approve',
    body: 'Nothing is sent, ever, without your click.',
  },
];

const steps = [
  {
    title: 'Upload once',
    body: 'Your CV, your municipalities, your minimum score. Pick Malmö and Lund, or open the doors to all of Skåne. The hunt respects the choice.',
  },
  {
    title: 'The hunt runs hourly',
    body: 'New ads are scraped, agency cross-posts are merged into the direct ad, and every survivor is scored against your CV. The rest never reach you.',
  },
  {
    title: 'Approve, tailor, send',
    body: 'One click and GLM writes a CV and cover letter shaped to the ad. The guard rejects any claim your real CV cannot back.',
  },
];

export default function LandingPage() {
  return (
    <div className="bg-paper text-ink">
      <header className="border-b border-paper-line">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5" aria-label="JobFinderOS home">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink">
              <Radar className="h-4 w-4 text-signal" aria-hidden />
            </span>
            <span className="font-display text-lg font-semibold tracking-tight">
              JobFinderOS
            </span>
          </Link>
          <nav
            className="hidden items-center gap-7 text-sm text-ink/65 sm:flex"
            aria-label="Landing sections"
          >
            <a href="#how" className="transition hover:text-ink">
              How it works
            </a>
            <a href="#sources" className="transition hover:text-ink">
              Sources
            </a>
            <a href="#guard" className="transition hover:text-ink">
              Fact guard
            </a>
          </nav>
          <Link
            href="/login"
            className="rounded-full border border-ink/15 px-4 py-1.5 text-sm font-medium text-ink transition hover:border-ink/45 active:scale-[0.98]"
          >
            Sign in
          </Link>
        </div>
      </header>

      <main>
        {/* Hero: four text elements, then the machine. */}
        <section className="overflow-hidden">
          <div className="mx-auto grid max-w-6xl items-center gap-14 px-6 pb-20 pt-16 sm:pt-20 lg:grid-cols-[1.05fr_0.95fr] lg:gap-12 lg:pb-28">
            <div>
              <p className="landing-rise num flex items-center gap-2.5 text-[11px] uppercase tracking-[0.18em] text-ink/55">
                <span className="relative flex h-2 w-2" aria-hidden>
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal/60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
                </span>
                Sweden + UK · hourly hunts
              </p>
              <h1 className="landing-rise landing-rise-1 mt-5 font-display text-5xl font-semibold leading-[1.04] tracking-[-0.02em] sm:text-6xl lg:text-[4.35rem]">
                Stop refreshing{' '}
                <em className="font-bold italic">job boards.</em>
              </h1>
              <p className="landing-rise landing-rise-2 mt-5 max-w-md text-lg leading-relaxed text-ink/70">
                JobFinderOS hunts Platsbanken and Reed every hour, scores every
                ad against your CV, and drafts applications you approve.
              </p>
              <div className="landing-rise landing-rise-3 mt-7 flex flex-wrap items-center gap-x-6 gap-y-4">
                <Link
                  href="/app"
                  className="rounded-xl bg-ink px-6 py-3.5 text-[15px] font-semibold text-paper transition hover:bg-ink/85 active:scale-[0.98]"
                >
                  Upload your CV
                </Link>
                <a
                  href="#how"
                  className="text-[15px] font-medium text-ink/70 underline decoration-paper-line decoration-2 underline-offset-8 transition hover:text-ink hover:decoration-signal"
                >
                  See how it works
                </a>
              </div>
            </div>
            <div className="landing-rise landing-rise-2 lg:pt-4 lg:pl-2">
              <CockpitPreview />
            </div>
          </div>
        </section>

        {/* Mechanism strip: sentence-led, editorial, no stat cards. */}
        <section aria-label="What you get" className="border-y border-paper-line">
          <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10 sm:flex-row sm:gap-0 sm:divide-x sm:divide-paper-line">
            {stats.map((s) => (
              <p
                key={s.value}
                className="max-w-xs text-sm leading-relaxed text-ink/60 sm:flex-1 sm:px-7 sm:first:pl-0 sm:last:pr-0"
              >
                <span className="num text-base font-semibold text-ink">
                  {s.value}.
                </span>{' '}
                {s.body}
              </p>
            ))}
          </div>
        </section>

        {/* How it works: a real pipeline sequence, so the numbers are earned. */}
        <section id="how" className="scroll-mt-16">
          <div className="mx-auto max-w-6xl px-6 py-20">
            <h2 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
              How it works
            </h2>
            <ol className="mt-10">
              {steps.map((step, i) => (
                <li
                  key={step.title}
                  className="grid gap-3 border-t border-paper-line py-8 last:border-b sm:grid-cols-[6.5rem_1fr] sm:gap-10"
                >
                  <p className="num pt-1 text-sm text-ink/40">
                    {String(i + 1).padStart(2, '0')}
                  </p>
                  <div>
                    <h3 className="font-display text-xl font-semibold tracking-tight">
                      {step.title}
                    </h3>
                    <p className="mt-2 max-w-2xl leading-relaxed text-ink/65">
                      {step.body}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Sources: asymmetric, Sweden first. */}
        <section id="sources" className="scroll-mt-16 bg-paper-deep">
          <div className="mx-auto max-w-6xl px-6 py-20">
            <h2 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
              Where we hunt
            </h2>
            <div className="mt-10 grid gap-12 lg:grid-cols-[1.4fr_1fr] lg:gap-16">
              <div>
                <h3 className="font-display text-2xl font-semibold tracking-tight">
                  Platsbanken, all of it.
                </h3>
                <p className="mt-3 max-w-lg leading-relaxed text-ink/65">
                  Sweden, through the official JobTech API, filtered to your
                  municipalities with their own taxonomy codes. Not fuzzy text
                  matching.
                </p>
              </div>
              <div className="lg:border-l lg:border-paper-line lg:pl-16">
                <h3 className="font-display text-2xl font-semibold tracking-tight">
                  Reed.co.uk
                </h3>
                <p className="mt-3 max-w-sm leading-relaxed text-ink/65">
                  Every sector of the UK job market, every day, scored by the
                  same engine as the Swedish ads.
                </p>
              </div>
            </div>
            <p className="num mt-12 border-t border-paper-line pt-6 text-xs uppercase tracking-[0.14em] text-ink/45">
              Public APIs only. No logins, no grey scraping.
            </p>
          </div>
        </section>

        {/* The guard: the quiet promise. */}
        <section id="guard" className="scroll-mt-16">
          <div className="mx-auto max-w-6xl px-6 py-20">
            <div className="max-w-2xl">
              <ShieldCheck className="h-8 w-8 text-ok" aria-hidden />
              <h2 className="mt-5 font-display text-3xl font-bold tracking-tight sm:text-4xl">
                Nothing invented. Ever.
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-ink/70">
                Every draft is checked line by line against your CV. If a claim
                is not in your history, it does not ship. No invented employers,
                no stretched dates, no phantom degrees.
              </p>
            </div>
          </div>
        </section>

        {/* Close: the amber button only ever appears on ink. */}
        <section className="bg-ink">
          <div className="mx-auto max-w-6xl px-6 py-20 text-center sm:py-24">
            <h2 className="font-display text-3xl font-bold tracking-tight text-paper sm:text-5xl">
              Your next job is already posted.
            </h2>
            <p className="mx-auto mt-4 max-w-md text-lg text-paper/60">
              Somewhere in tonight&apos;s hunt.
            </p>
            <Link
              href="/app"
              className="mt-9 inline-block rounded-xl bg-signal px-7 py-3.5 text-[15px] font-semibold text-ink transition hover:bg-signal/90 active:scale-[0.98]"
            >
              Upload your CV
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-paper-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-8 sm:flex-row sm:items-center sm:justify-between">
          <p className="num text-[10px] uppercase tracking-[0.16em] text-ink/45">
            JobFinderOS
          </p>
          <p className="num text-[10px] uppercase tracking-[0.16em] text-ink/45">
            Data: JobTech (Platsbanken) · Reed.co.uk
          </p>
          <p className="num text-[10px] uppercase tracking-[0.16em] text-ink/45">
            Made in Malmö
          </p>
        </div>
      </footer>
    </div>
  );
}
