#!/usr/bin/env bash
# WO-07 post-deploy verification — one command, every gate.
# Usage: bash ops/verify_deployment.sh [https://jobfinderos-api.onrender.com] [https://jobfinderos.pages.dev]
#
# Checks: API /health (DB up), CORS preflight from the frontend origin,
# a full register/login/me roundtrip against the live API (creates one
# clearly-named probe account), and that the Pages site serves the app
# with the API URL inlined in its bundle.
set -uo pipefail

API="${1:-https://jobfinderos-api.onrender.com}"
FRONTEND="${2:-https://jobfinderos.pages.dev}"
PROBE_EMAIL="deploy-check@jobfinderos.dev"
PROBE_PASS="DeployCheck-$(date +%s)!"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }

echo "== API: $API =="

# 1. /health — 200 + status ok + database up
body=$(curl -s -m 60 "$API/health" || true)
code=$(curl -s -o /dev/null -w "%{http_code}" -m 60 "$API/health" || true)
if [ "$code" = "200" ] && echo "$body" | grep -q '"database":"up"'; then
    ok "/health 200, database up"
else
    bad "/health (code=$code body=$body)"
    echo "  -> if 404: blueprint not applied / wrong service name."
    echo "  -> if spin-down: first request takes ~1 min on the free plan, retry."
fi

# 2. CORS preflight from the frontend origin — origin must be echoed
#    (allow_credentials=True means a wildcard here would be the defect)
cors=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -X OPTIONS "$API/api/v1/auth/jwt/login" \
    -H "Origin: $FRONTEND" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: content-type" || true)
allow=$(curl -s -m 30 -D - -o /dev/null -X OPTIONS "$API/api/v1/auth/jwt/login" \
    -H "Origin: $FRONTEND" \
    -H "Access-Control-Request-Method: POST" | tr -d '\r' | grep -i '^access-control-allow-origin:' || true)
if [ "$cors" = "200" ] && echo "$allow" | grep -q "$FRONTEND"; then
    ok "CORS preflight from $FRONTEND allowed"
else
    bad "CORS preflight (code=$cors allow='$allow')"
    echo "  -> fix: Render api env CORS_ORIGINS must list $FRONTEND exactly."
fi

# 3. Auth roundtrip: register -> login -> me (proves live DB writes)
reg=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/auth/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$PROBE_EMAIL\",\"password\":\"$PROBE_PASS\"}" || true)
[ "$reg" = "201" ] || [ "$reg" = "400" ] && ok "register ($reg; 400 = probe exists from a previous run)" || bad "register ($reg)"
login=$(curl -s -m 30 -X POST "$API/api/v1/auth/jwt/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=$PROBE_EMAIL" --data-urlencode "password=$PROBE_PASS" || true)
token=$(echo "$login" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "$token" ]; then
    me=$(curl -s -m 30 -H "Authorization: Bearer $token" "$API/api/v1/users/me" || true)
    echo "$me" | grep -q "$PROBE_EMAIL" && ok "login + /users/me roundtrip" || bad "/users/me ($me)"
else
    bad "login (no access_token; response: ${login:0:120})"
fi

echo "== Frontend: $FRONTEND =="
fcode=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -L "$FRONTEND" || true)
if [ "$fcode" = "200" ]; then
    ok "site serves 200"
    bundle=$(curl -s -m 30 -L "$FRONTEND" | grep -o '/_next/static/chunks/[^"]*\.js' | head -1 || true)
    if [ -n "$bundle" ] && curl -s -m 30 "$FRONTEND$bundle" | grep -q "$(echo "$API" | sed 's|https://||')"; then
        ok "API URL inlined in the client bundle"
    else
        bad "API URL not found in bundle ($bundle)"
        echo "  -> fix: CF Pages env NEXT_PUBLIC_API_URL=$API, then redeploy."
    fi
else
    bad "site (code=$fcode)"
    echo "  -> if 000/DNS: the Pages project doesn't exist yet (runbook Step 3)."
fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ] || exit 1
