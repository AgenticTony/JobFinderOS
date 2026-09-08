#!/usr/bin/env bash
# WO-07 post-deploy verification — one command, every gate.
# Usage: bash ops/verify_deployment.sh [https://jobfinderos-api.onrender.com] [https://jobfinderos.pages.dev] [https://<ref>.supabase.co]
#
# MIG-WO2: the auth roundtrip is a REAL Supabase password-grant login
# (the API has no auth endpoints anymore — it verifies Supabase JWTs).
# The probe account must exist IN SUPABASE (create once in the dashboard
# or via the admin API; it mirrors into the API users table on first
# authenticated request). Export SUPABASE_ANON_KEY (publishable key) or
# pass nothing and the auth check degrades to a skip.
#
# Checks: API /health (DB up), CORS preflight from the frontend origin,
# Supabase login -> API authenticated call (proves JWKS verify + live DB
# reads), and that the Pages site serves the app with the API URL
# inlined in its bundle.
set -uo pipefail

API="${1:-https://jobfinderos-api.onrender.com}"
FRONTEND="${2:-https://jobfinderos.pages.dev}"
SUPABASE="${3:-${SUPABASE_URL:-https://jsibogzklhswpmozcyhn.supabase.co}}"
PROBE_EMAIL="deploy-check@jobfinderos.dev"
PROBE_PASS="DeployCheck-Probe-2026!"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }

echo "== API: $API =="

# 1. /health — 200 + status ok + database up. Free instances spin down
#    after 15 min idle and Render's edge answers the FIRST request with a
#    plain-text 404 while the instance wakes (~1 min worst case) — retry.
body=""; code="000"
for attempt in 1 2 3 4 5 6; do
    body=$(curl -s -m 90 "$API/health" || true)
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 90 "$API/health" || true)
    if [ "$code" = "200" ]; then break; fi
    echo "  (wake attempt $attempt: code=$code — free-instance cold start, retrying)"
    sleep 12
done
if [ "$code" = "200" ] && echo "$body" | grep -q '"database":"up"'; then
    ok "/health 200, database up"
else
    bad "/health (code=$code body=$body)"
    echo "  -> if 404 persists: blueprint not applied / wrong service name."
fi

# 2. CORS preflight from the frontend origin — ONE request, capture code
#    and headers together (two requests straddle a cold-start wake). The
#    origin must be ECHOED (allow_credentials=True forbids wildcard).
#    MIG-WO2: preflight a live business route; the old auth endpoints
#    are gone (CORSMiddleware answers before routing either way).
cors_headers=$(mktemp)
cors=$(curl -s -m 60 -o /dev/null -D "$cors_headers" -w "%{http_code}" -X OPTIONS "$API/api/v1/profile/status" \
    -H "Origin: $FRONTEND" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: authorization,content-type" || true)
allow=$(tr -d '\r' < "$cors_headers" | grep -i '^access-control-allow-origin:' || true)
rm -f "$cors_headers"
if [ "$cors" = "200" ] && echo "$allow" | grep -q "$FRONTEND"; then
    ok "CORS preflight from $FRONTEND allowed"
else
    bad "CORS preflight (code=$cors allow='$allow')"
    echo "  -> fix: Render api env CORS_ORIGINS must list $FRONTEND exactly."
fi

# 3. Auth roundtrip (MIG-WO2): REAL Supabase password-grant login, then
#    the token against an authenticated API route. 404 'No CV on file'
#    from /profile/status = AUTH PASSED (anonymous is 401; a broken JWKS
#    verify is 401). /account/export 200 = live DB reads through the
#    mirror row.
if [ -z "${SUPABASE_ANON_KEY:-}" ]; then
    echo "  (auth roundtrip SKIPPED — export SUPABASE_ANON_KEY (publishable) to run it)"
    token=""
else
    login=$(curl -s -m 30 -X POST "$SUPABASE/auth/v1/token?grant_type=password" \
        -H "apikey: $SUPABASE_ANON_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$PROBE_EMAIL\",\"password\":\"$PROBE_PASS\"}" || true)
    token=$(echo "$login" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
    if [ -n "$token" ]; then
        ok "Supabase password-grant login"
    else
        bad "Supabase login (response: ${login:0:160})"
        echo "  -> create the probe account in Supabase (dashboard or admin API) with the credentials at the top of this script."
    fi
fi
if [ -n "$token" ]; then
    # retry wrapper — Render's free edge intermittently 404s while waking.
    # Body and status split with sed '$d' (portable): BSD head rejects
    # `head -n -1`, which would empty $me on macOS and fail the check on
    # a healthy deploy (review finding 2026-09-08).
    me=""; code="000"
    for attempt in 1 2 3; do
        me=$(curl -s -m 60 -w "\n%{http_code}" -H "Authorization: Bearer $token" "$API/api/v1/account/export" || true)
        code=$(echo "$me" | tail -1); me=$(echo "$me" | sed '$d')
        [ "$code" = "200" ] && break
        echo "  (api wake attempt $attempt — code=$code, retrying)"
        sleep 8
    done
    if [ "$code" = "200" ] && echo "$me" | grep -q "$PROBE_EMAIL"; then
        ok "API authenticated export (JWKS verify + mirror row + DB read)"
    elif [ "$code" = "401" ]; then
        bad "API rejected the Supabase token (401) — JWKS verify broken; check SUPABASE_URL on the deployment"
    else
        bad "API export (code=$code body=${me:0:120})"
    fi
fi

echo "== Frontend: $FRONTEND =="
fcode=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -L "$FRONTEND" || true)
if [ "$fcode" = "200" ]; then
    ok "site serves 200"
    # scan EVERY chunk referenced by the page — the API URL lives in
    # whichever chunk compiled src/lib/api.ts, not necessarily the first
    found=0
    for c in $(curl -s -m 30 -L "$FRONTEND" | grep -o '/_next/static/chunks/[^"]*\.js' | sort -u); do
        if curl -s -m 30 "$FRONTEND$c" | grep -q "$(echo "$API" | sed 's|https://||')"; then
            found=1; break
        fi
    done
    if [ "$found" = "1" ]; then
        ok "API URL inlined in the client bundle"
    else
        bad "API URL not found in any chunk"
        echo "  -> fix: rebuild with NEXT_PUBLIC_API_URL=$API and redeploy (ops/deploy_frontend.sh)."
    fi
else
    bad "site (code=$fcode)"
    echo "  -> if 000/DNS: the Pages project doesn't exist yet (runbook Step 3)."
fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ] || exit 1
