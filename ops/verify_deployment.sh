#!/usr/bin/env bash
# WO-07 post-deploy verification — one command, every gate.
# Usage: bash ops/verify_deployment.sh [https://jobfinderos-api.onrender.com] [https://jobfinderos.pages.dev]
#
# Checks: API /health (DB up), CORS preflight from the frontend origin,
# a full register/login/me roundtrip against the live API (reuses ONE
# permanent, clearly-named service account — run 1 registers it, later
# runs get the accepted 400 'already exists' and log straight in), email-apply provisioning (P0-6:
# /api/v1/profile/status reports email_apply_enabled from RESEND_API_KEY),
# and that the Pages site serves the app with the API URL inlined in its
# bundle. Manual remainder: only a real apply proves APPLY_FROM_EMAIL is
# a Resend-VERIFIED sender.
set -uo pipefail

API="${1:-https://jobfinderos-api.onrender.com}"
FRONTEND="${2:-https://jobfinderos.pages.dev}"
# One FIXED service account. The earlier unique-per-run address (plus-
# addressing timestamp) was itself the workaround for a timestamped
# PASSWORD breaking re-runs — but register's 400 'already exists' is
# already accepted below and the login roundtrip proves the fixed
# credentials, so a permanent account verifies exactly the same paths
# without accumulating a probe row in users per deploy (294 test rows
# were cleaned out of production on 2026-09-01; don't regrow them).
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
cors_headers=$(mktemp)
cors=$(curl -s -m 60 -o /dev/null -D "$cors_headers" -w "%{http_code}" -X OPTIONS "$API/api/v1/auth/jwt/login" \
    -H "Origin: $FRONTEND" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: content-type" || true)
allow=$(tr -d '\r' < "$cors_headers" | grep -i '^access-control-allow-origin:' || true)
rm -f "$cors_headers"
if [ "$cors" = "200" ] && echo "$allow" | grep -q "$FRONTEND"; then
    ok "CORS preflight from $FRONTEND allowed"
else
    bad "CORS preflight (code=$cors allow='$allow')"
    echo "  -> fix: Render api env CORS_ORIGINS must list $FRONTEND exactly."
fi

# 3. Auth roundtrip: register -> login -> me (proves live DB writes).
#    Render's free edge intermittently answers 404 'no-server' while the
#    instance is half-awake — retry the login rather than misdiagnosing.
reg=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/auth/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$PROBE_EMAIL\",\"password\":\"$PROBE_PASS\"}" || true)
[ "$reg" = "201" ] || [ "$reg" = "400" ] && ok "register ($reg)" || bad "register ($reg)"
login=""; token=""
for attempt in 1 2 3 4 5; do
    login=$(curl -s -m 60 -X POST "$API/api/v1/auth/jwt/login" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "username=$PROBE_EMAIL" --data-urlencode "password=$PROBE_PASS" || true)
    token=$(echo "$login" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
    [ -n "$token" ] && break
    echo "  (login wake attempt $attempt — free-edge no-server, retrying)"
    sleep 8
done
if [ -n "$token" ]; then
    me=$(curl -s -m 30 -H "Authorization: Bearer $token" "$API/api/v1/users/me" || true)
    echo "$me" | grep -q "$PROBE_EMAIL" && ok "login + /users/me roundtrip" || bad "/users/me ($me)"
else
    bad "login (no access_token; response: ${login:0:120})"
fi

# 4. Email apply provisioned (P0-6). The flagship send-with-PDFs loop is
#    dead without RESEND_API_KEY — profile /status derives
#    email_apply_enabled from it — and neither key appears anywhere a
#    health check would notice. Verified through the roundtrip's own
#    token, so this reads the DEPLOYED env, not the yaml. Needs the
#    login above; if that failed, fix it first.
#    URL note: the router is mounted at the SINGULAR /api/v1/profile
#    (main.py include_router) — profiles/status 404s.
if [ -n "$token" ]; then
    pstatus=$(curl -s -m 30 -H "Authorization: Bearer $token" "$API/api/v1/profile/status" || true)
    if echo "$pstatus" | grep -q '"email_apply_enabled":true'; then
        ok "email apply enabled (RESEND_API_KEY set on the deployment)"
    else
        bad "email apply disabled (RESEND_API_KEY unset: $pstatus)"
        echo "  -> fix: Render api env — set RESEND_API_KEY and APPLY_FROM_EMAIL (runbook Step 2)."
    fi
else
    echo "  (email-apply check skipped — no token; fix the login failure above first)"
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
