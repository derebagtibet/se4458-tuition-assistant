# Kong declarative (DB-less) configuration — API Gateway for the Tuition REST API.
# Rendered at container start by render-and-start.sh, which substitutes:
#   ${TUITION_API_URL}  upstream URL of the Tuition REST API (e.g. http://tuition-api:8000)
#   ${JWT_ISSUER}       JWT `iss` claim == Kong JWT credential key
#   ${JWT_SECRET}       HS256 secret shared with the Tuition API (token issuer)
#   ${MOBILE_DAILY_LIMIT} mobile Query Tuition calls per student per day (3)
_format_version: "3.0"
_transform: true

services:
  - name: tuition-api-v1
    url: ${TUITION_API_URL}
    connect_timeout: 5000
    read_timeout: 30000
    routes:
      # ---------------------------------------------------------------- public
      - name: auth-v1
        paths: ["/api/v1/auth"]
        methods: ["POST"]
        strip_path: false

      - name: api-docs
        paths: ["/api/docs", "/api/openapi.json"]
        methods: ["GET"]
        strip_path: false

      # University Mobile App: no authentication, 3 calls per STUDENT per day.
      - name: mobile-tuition-v1
        paths: ["/api/v1/mobile/tuition"]
        methods: ["GET"]
        strip_path: false
        plugins:
          - name: pre-function
            config:
              access:
                - |
                  -- Per-student daily quota, keyed by the student number in the path (not client IP).
                  local limit = ${MOBILE_DAILY_LIMIT}
                  local sn = kong.request.get_path():match("^/api/v1/mobile/tuition/([^/]+)")
                  if not sn then return end
                  sn = string.upper(ngx.unescape_uri(sn))
                  local now = ngx.time() + 3 * 3600          -- Europe/Istanbul (UTC+3, no DST)
                  local day = os.date("!%Y-%m-%d", now)
                  local seconds_left = 86400 - (now % 86400)
                  local dict = ngx.shared.kong_rate_limiting_counters
                  local count, err = dict:incr("mobile-tuition:" .. sn .. ":" .. day, 1, 0, 172800)
                  if not count then
                    kong.log.err("student quota counter failed: ", err)  -- fail open, API also enforces
                    return
                  end
                  kong.response.set_header("X-RateLimit-Limit-Student-Day", limit)
                  kong.response.set_header("X-RateLimit-Remaining-Student-Day", math.max(0, limit - count))
                  if count > limit then
                    return kong.response.exit(429, {
                      error = { code = "DAILY_LIMIT_EXCEEDED", source = "api-gateway",
                                message = "Query Tuition is limited to " .. limit .. " calls per student per day",
                                details = { studentNo = sn, day = day } }
                    }, { ["Retry-After"] = tostring(seconds_left) })
                  end

      # ---------------------------------------------------------------- protected (JWT)
      - name: banking-v1
        paths: ["/api/v1/banking"]
        methods: ["GET", "POST"]
        strip_path: false
        plugins:
          - name: jwt
            config:
              key_claim_name: iss
              claims_to_verify: ["exp"]
              header_names: ["authorization"]
              run_on_preflight: false

      - name: admin-v1
        paths: ["/api/v1/admin"]
        methods: ["GET", "POST"]
        strip_path: false
        plugins:
          - name: jwt
            config:
              key_claim_name: iss
              claims_to_verify: ["exp"]
              header_names: ["authorization"]
          - name: request-size-limiting
            config:
              allowed_payload_size: 1
              size_unit: megabytes

consumers:
  # All tokens are minted by the Tuition API auth endpoint (iss = ${JWT_ISSUER}).
  # Kong verifies signature + expiry; the API then enforces role/ownership (student vs bank vs admin).
  - username: tuition-auth-issuer
    jwt_secrets:
      - key: "${JWT_ISSUER}"
        secret: "${JWT_SECRET}"
        algorithm: HS256

plugins:
  # Global: request id for tracing + coarse per-IP abuse protection on every route.
  - name: correlation-id
    config:
      header_name: X-Request-ID
      generator: uuid
      echo_downstream: true
  - name: rate-limiting
    config:
      minute: 120
      limit_by: ip
      policy: local
      fault_tolerant: true
