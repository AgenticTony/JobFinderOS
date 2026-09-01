import type { Metadata } from 'next';
import Script from 'next/script';
import { Familjen_Grotesk, Geist, Geist_Mono } from 'next/font/google';
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
        {/* Google Tag Manager + Google Analytics (owner, 2026-09-01).
            afterInteractive keeps the static export's first paint free
            of third-party scripts. */}
        <Script id="gtm-init" strategy="afterInteractive">
          {`(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-MBKZHZVB');`}
        </Script>
        <Script
          src="https://www.googletagmanager.com/gtag/js?id=G-YHFHWWL4TF"
          strategy="afterInteractive"
        />
        <Script id="ga-init" strategy="afterInteractive">
          {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', 'G-YHFHWWL4TF');`}
        </Script>
      </body>
    </html>
  );
}
