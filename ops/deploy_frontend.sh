#!/usr/bin/env bash
# WO-07: build + deploy the frontend to Cloudflare Pages (direct upload).
#
# The Pages project `jobfinderos` is DIRECT-UPLOAD (created via wrangler,
# not Git-connected) — deploys happen through this script, they do not
# follow git push. Run after frontend changes reach main:
#
#     bash ops/deploy_frontend.sh
#
# Requires: `npx wrangler login` once (OAuth, browser click).
set -euo pipefail

API_URL="${API_URL:-https://jobfinderos-api.onrender.com}"
PROJECT="${PAGES_PROJECT:-jobfinderos}"

# MIG-WO2: Supabase Auth client config (PUBLISHABLE values only — never
# the service-role key). Export them once: `export NEXT_PUBLIC_SUPABASE_URL=...`
# The build FAILS without them (next.config.ts guard) — deliberate: a
# bundle missing these ships a login that cannot authenticate.
: "${NEXT_PUBLIC_SUPABASE_URL:?export NEXT_PUBLIC_SUPABASE_URL (https://<ref>.supabase.co) first}"
: "${NEXT_PUBLIC_SUPABASE_ANON_KEY:?export NEXT_PUBLIC_SUPABASE_ANON_KEY (publishable anon key — NOT the service key) first}"

cd "$(dirname "$0")/../frontend"

echo "building static export (NEXT_PUBLIC_API_URL=$API_URL, SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL)..."
NEXT_PUBLIC_API_URL="$API_URL" npm run build

test -f out/index.html || { echo "build did not produce out/index.html" >&2; exit 1; }

echo "deploying out/ to Cloudflare Pages project '$PROJECT'..."
npx -y wrangler@latest pages deploy out --project-name "$PROJECT" --branch main --commit-dirty=true
