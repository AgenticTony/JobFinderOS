import type { Metadata } from 'next';
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
    'Twice-daily hunts across Platsbanken and Reed, every ad scored against your CV. Applications you approve, drafts that never invent facts.',
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
        {children}
      </body>
    </html>
  );
}
