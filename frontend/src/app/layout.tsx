import type { Metadata } from 'next';
import { Familjen_Grotesk, Geist, Geist_Mono } from 'next/font/google';
import AnalyticsGate from '@/components/AnalyticsGate';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

const familjen = Familjen_Grotesk({
  variable: '--font-familjen',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'JobFinderOS',
  description:
    "Twice-daily hunts across Sweden's and the UK's job markets, every ad scored against your CV. Applications you approve, drafts that never invent facts.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} ${familjen.variable}`}
    >
      {/* suppressHydrationWarning: browser extensions (e.g. the one adding
          cz-shortcut-listen) inject attributes into <body> before React
          hydrates, causing spurious mismatch warnings. This suppresses
          attribute-level warnings on this element only. */}
      <body className="antialiased" suppressHydrationWarning>
        {/* Cookiebot consent banner — loads unconditionally (it must,
            to ask). Raw script per Cookiebot's install spec; React 19
            hoists it into <head>. */}
        <script
          id="Cookiebot"
          src="https://consent.cookiebot.com/uc.js"
          data-cbid="631a989b-0195-4173-ac38-1e82867cec37"
          async
        />
        {/* Google Tag Manager (noscript) — first thing inside <body>,
            per GTM's install spec. */}
        <noscript>
          <iframe
            src="https://www.googletagmanager.com/ns.html?id=GTM-MBKZHZVB"
            height="0"
            width="0"
            style={{ display: 'none', visibility: 'hidden' }}
          />
        </noscript>
        {children}
        {/* GTM + GA4 mount only after Cookiebot statistics consent —
            decline and no Google script executes at all. */}
        <AnalyticsGate />
      </body>
    </html>
  );
}
