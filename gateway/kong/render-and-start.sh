#!/bin/sh
# Render kong.yml from the template using environment variables, then start Kong (DB-less).
set -eu
: "${TUITION_API_URL:?TUITION_API_URL is required}"
: "${JWT_SECRET:?JWT_SECRET is required}"
JWT_ISSUER="${JWT_ISSUER:-tuition-auth}"
MOBILE_DAILY_LIMIT="${MOBILE_DAILY_LIMIT:-3}"
esc() { printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'; }
sed -e "s|\${TUITION_API_URL}|$(esc "$TUITION_API_URL")|g" \
    -e "s|\${JWT_ISSUER}|$(esc "$JWT_ISSUER")|g" \
    -e "s|\${JWT_SECRET}|$(esc "$JWT_SECRET")|g" \
    -e "s|\${MOBILE_DAILY_LIMIT}|$(esc "$MOBILE_DAILY_LIMIT")|g" \
    /kong/kong.yml.tpl > /tmp/kong.yml
export KONG_DECLARATIVE_CONFIG=/tmp/kong.yml
exec /docker-entrypoint.sh kong docker-start
