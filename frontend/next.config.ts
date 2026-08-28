import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // WO-07: the frontend deploys to Cloudflare Pages as a STATIC export
  // (the official "Next.js (Static HTML Export)" path —
  // developers.cloudflare.com/pages/framework-guides/nextjs/deploy-a-static-nextjs-site;
  // build `npx next build`, output directory `out/`). The app is fully
  // client-side — axios against the API (src/lib/api.ts), zustand state —
  // so nothing here needs a Next server. `next dev` is unaffected.
  output: 'export',
  // Static export cannot use the Next image optimizer (it needs a server);
  // unoptimized keeps any future next/image usage export-safe.
  images: { unoptimized: true },
};

export default nextConfig;
