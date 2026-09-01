'use client';

// Analytics runs only after Cookiebot statistics consent (owner
// decision 2026-09-01 — EU testers). The Cookiebot loader itself is in
// the root layout and runs unconditionally (it must, to show the
// banner); this gate mounts GTM + GA4 ONLY when consent was given.
// No consent, or Cookiebot blocked/failed -> no Google script ever
// executes on the page.

import { useEffect, useState } from 'react';
import Script from 'next/script';

declare global {
  interface Window {
    Cookiebot?: { consent?: { statistics?: boolean } };
  }
}

export default function AnalyticsGate() {
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    const sync = () =>
      setAllowed(Boolean(window.Cookiebot?.consent?.statistics));
    sync(); // returning visitor whose consent predates this page load
    window.addEventListener('CookiebotOnConsentReady', sync);
    return () => window.removeEventListener('CookiebotOnConsentReady', sync);
  }, []);

  if (!allowed) return null;

  return (
    <>
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
    </>
  );
}
