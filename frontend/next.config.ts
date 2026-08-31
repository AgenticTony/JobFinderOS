import type { NextConfig } from 'next';

// Fail the BUILD, not the deploy (review r4): src/lib/api.ts falls back to
// http://localhost:8000 when NEXT_PUBLIC_API_URL is unset — and a local
// .env.local provides it at BUILD time too, silently baking localhost
// into the deployed bundle (deploy succeeds, every user's browser calls
// their own machine). Verified red: builds used to succeed with
// localhost in the emitted chunks.
if (process.env.NODE_ENV === 'production') {
  const api = process.env.NEXT_PUBLIC_API_URL ?? '';
  let host = '';
  try {
    host = api ? new URL(api).hostname : '';
  } catch {
    throw new Error(`NEXT_PUBLIC_API_URL is not a valid URL: ${api!}`);
  }
  if (!host || host === 'localhost' || host.startsWith('127.')) {
    throw new Error(
      'Production builds need a real NEXT_PUBLIC_API_URL (got ' +
        `${JSON.stringify(api)} — a missing value falls back to localhost, ` +
        'and .env.local injects it at build time). Set it in CI, ' +
        'ops/deploy_frontend.sh, or the CF Pages build env, e.g. ' +
        'NEXT_PUBLIC_API_URL=https://jobfinderos-api.onrender.com.',
    );
  }
}

const nextConfig: NextConfig = {
  // WO-07: the frontend deploys to Cloudflare Pages as a STATIC export
  // (the official "Next.js (Static HTML Export)" path —
  // developers.cloudflare.com/pages/framework-guides/nextjs/deploy-a-static-nextjs-site;
  // build `npx next build`, output directory `out/`). The app is fully
  // client-side — axios against the API (src/lib/api.ts), React state —
  // so nothing here needs a Next server. `next dev` is unaffected.
  // (HYGIENE: `start` is aligned to `next dev` — `next start` errors under
  // output:'export' since there is no server bundle to start.)
  output: 'export',
  // Static export cannot use the Next image optimizer (it needs a server);
  // unoptimized keeps any future next/image usage export-safe.
  images: { unoptimized: true },
};

export default nextConfig;
