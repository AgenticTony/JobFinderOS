import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'JobFinderOS',
  description:
    'Your job hunting operating system — scrape, match against your CV, approve, apply. Built on the TalentHive engine.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
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
