#!/usr/bin/env bash
# Exercises the API Gateway: public route, protected route (401 without / 200 with JWT), the per-student
# 3/day mobile limit (429), a payment + duplicate (409), admin batch upload and paged unpaid list.
#   GATEWAY_URL=https://<your-kong-fqdn> bash scripts/gateway_demo.sh
set -uo pipefail
GW="${GATEWAY_URL:-http://localhost:8000}"
B="$GW/api/v1"
STU="${STUDENT_NO:-S1004}"
j() { python3 -m json.tool 2>/dev/null || cat; }
tok() { curl -s -X POST "$B/auth/token" -H 'Content-Type: application/json' \
          -d "{\"username\":\"$1\",\"password\":\"$2\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "1. Public route via gateway: Mobile Query Tuition ($STU), shows per-student quota headers"
curl -s -i "$B/mobile/tuition/$STU" | grep -Ei '^(HTTP|x-ratelimit|x-student|x-request-id)|^\{' 

step "2. Same student 3 more times -> 4th call in the day must be 429 (keyed by student, not IP)"
for i in 2 3 4; do curl -s -o /tmp/gw_body -w "call $i -> HTTP %{http_code}\n" "$B/mobile/tuition/$STU"; done
j < /tmp/gw_body

step "3. Protected route WITHOUT token -> 401 from the gateway"
curl -s -i "$B/banking/tuition/S1001" | grep -Ei '^HTTP|^\{|message'

step "4. Login (JWT) and call protected route WITH token -> 200"
T=$(tok s1001 'Passw0rd!')
curl -s "$B/banking/tuition/S1001?term=2026-FALL" -H "Authorization: Bearer $T" | j

step "5. Student token used for ANOTHER student -> 403 (authorization in the API)"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$B/banking/tuition/S1002" -H "Authorization: Bearer $T"

step "6. Simulated payment + duplicate transaction id"
TX="demo-$(date +%s)"
curl -s -X POST "$B/banking/payments" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d "{\"studentNo\":\"S1001\",\"term\":\"2026-FALL\",\"amount\":\"100.00\",\"transactionId\":\"$TX\"}" | j
curl -s -X POST "$B/banking/payments" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d "{\"studentNo\":\"S1001\",\"term\":\"2026-FALL\",\"amount\":\"100.00\",\"transactionId\":\"$TX\"}" | j

step "7. Admin: batch CSV upload + paged unpaid list"
A=$(tok admin 'Admin123!')
curl -s -X POST "$B/admin/tuition/batch" -H "Authorization: Bearer $A" -F "file=@data/sample_tuition_batch.csv" | j
curl -s "$B/admin/tuition/unpaid?term=2026-FALL&page=1&pageSize=3" -H "Authorization: Bearer $A" | j
