#!/usr/bin/env bash
# Deploys the whole stack to Azure (Azure for Students works):
#   Azure Container Registry  -> images (built in the cloud with `az acr build`, no local Docker needed)
#   Azure Database for PostgreSQL Flexible Server (Burstable B1ms) -> server database
#   Azure Container Apps: tuition-api (INTERNAL ingress), mcp-server (external), kong-gateway (external)
#
# Usage (from the repo root, after `az login`):
#   bash deploy/azure/deploy.sh
# Optional overrides: LOCATION, RG, PREFIX.   Tear down when graded:  az group delete -n "$RG" --yes
set -euo pipefail

LOCATION="${LOCATION:-westeurope}"
RG="${RG:-se4458-tuition-rg}"
PREFIX="${PREFIX:-se4458tuition$RANDOM}"
ACR="${PREFIX}acr"
PG="${PREFIX}-pg"
ENV_NAME="tuition-env"
TAG="${TAG:-1.0.0}"
STATE_FILE="deploy/azure/.deploy-state"   # keeps generated names/secrets for re-runs (git-ignored)

if [[ -f "$STATE_FILE" ]]; then source "$STATE_FILE"; fi
PG_PASS="${PG_PASS:-Pg$(openssl rand -hex 12)A1}"
JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 32)}"
cat > "$STATE_FILE" <<EOF
PREFIX=$PREFIX
ACR=$ACR
PG=$PG
PG_PASS=$PG_PASS
JWT_SECRET=$JWT_SECRET
${PG_LOCATION:+PG_LOCATION=$PG_LOCATION}
EOF

echo ">> Resource group $RG ($LOCATION)"
echo ">> Azure CLI containerapp extension"
if az extension show --name containerapp -o none 2>/dev/null; then
  echo "   already installed"
else
  az extension add --name containerapp --yes --only-show-errors
fi

echo ">> Resource providers (first time on a new subscription can take several minutes)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL; do
  state=$(az provider show -n "$ns" --query registrationState -o tsv 2>/dev/null | tr -d '\r' || echo NotRegistered)
  if [[ "$state" != "Registered" ]]; then
    echo "   registering $ns ($state)"
    az provider register --namespace "$ns" -o none
  fi
done
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL; do
  for i in $(seq 1 60); do
    state=$(az provider show -n "$ns" --query registrationState -o tsv | tr -d '\r')
    [[ "$state" == "Registered" ]] && break
    sleep 10
  done
  echo "   $ns: $state"
  [[ "$state" == "Registered" ]] || { echo "Provider $ns is still $state after 10 minutes; re-run the script later."; exit 1; }
done
az group create -n "$RG" -l "$LOCATION" -o none

# Cloud build without streaming logs: on Windows the Azure CLI crashes while printing build
# logs in non-UTF-8 consoles (e.g. cp1254). The build still runs in ACR; we wait and check status.
acr_build() {  # acr_build <image:tag> <context>
  if [[ -z "${FORCE_BUILD:-}" ]] && az acr repository show -n "$ACR" --image "$1" -o none 2>/dev/null; then
    echo "   $1 already in registry, skipping (FORCE_BUILD=1 or a new TAG rebuilds)"; return
  fi
  echo "   building $1 (in ACR, takes 1-3 min)"
  local status
  status=$(az acr build -r "$ACR" -t "$1" "$2" --no-logs --query status -o tsv | tr -d '\r')
  echo "   $1: $status"
  [[ "$status" == "Succeeded" ]] || { echo "ACR build of $1 failed. See: az acr task list-runs -r $ACR -o table"; exit 1; }
}

echo ">> Container registry $ACR + cloud builds"
az acr show -n "$ACR" -o none 2>/dev/null || az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled true -o none
acr_build "tuition-api:$TAG" services/tuition_api
# MCP image needs the policy document inside its build context
cp data/University_Tuition_Policies.md services/mcp_server/data/
acr_build "mcp-server:$TAG" services/mcp_server
acr_build "kong-gateway:$TAG" gateway/kong
ACR_SERVER=$(az acr show -n "$ACR" --query loginServer -o tsv | tr -d '\r')
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv | tr -d '\r')
ACR_PASS=$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv | tr -d '\r')

echo ">> PostgreSQL flexible server $PG (takes a few minutes)"
# Student subscriptions often cannot use Burstable B1ms in every region, so try a list of
# regions (override with PG_LOCATION=<region>). The chosen region is kept in the state file.
if ! az postgres flexible-server show -g "$RG" -n "$PG" -o none 2>/dev/null; then
  PG_CREATED=""
  for PG_REGION in ${PG_LOCATION:-$LOCATION northeurope swedencentral polandcentral germanywestcentral francecentral uksouth}; do
    echo "   trying region $PG_REGION"
    if az postgres flexible-server create -g "$RG" -n "$PG" -l "$PG_REGION" --tier Burstable --sku-name Standard_B1ms \
        --storage-size 32 --version 16 --admin-user tuitionadmin --admin-password "$PG_PASS" \
        --public-access 0.0.0.0 --yes -o none; then   # 0.0.0.0 = allow Azure services only
      PG_CREATED="$PG_REGION"; echo "PG_LOCATION=$PG_REGION" >> "$STATE_FILE"; break
    fi
  done
  [[ -n "$PG_CREATED" ]] || { echo "Could not create PostgreSQL B1ms in any tried region. Set PG_LOCATION=<region> and re-run."; exit 1; }
  echo "   PostgreSQL created in $PG_CREATED"
else
  echo "   already exists"
fi
if az postgres flexible-server db list --resource-group "$RG" --server-name "$PG" --query "[].name" -o tsv | tr -d '\r' | grep -qx tuition; then
  echo "   database 'tuition' already exists"
else
  az postgres flexible-server db create --resource-group "$RG" --server-name "$PG" --name tuition -o none
  echo "   database 'tuition' created"
fi
DB_URL="postgresql+psycopg://tuitionadmin:${PG_PASS}@${PG}.postgres.database.azure.com:5432/tuition?sslmode=require"

echo ">> Container Apps environment"
env_state() { az containerapp env show -g "$RG" -n "$ENV_NAME" --query properties.provisioningState -o tsv 2>/dev/null | tr -d '\r' || true; }
ENV_STATE=$(env_state)
if [[ "$ENV_STATE" == "Succeeded" ]]; then
  echo "   already exists"
else
  for ENV_REGION in ${ENV_LOCATION:-$LOCATION northeurope swedencentral polandcentral germanywestcentral francecentral}; do
    if [[ -n "$ENV_STATE" ]]; then
      echo "   removing environment in state '$ENV_STATE'"
      az containerapp env delete -g "$RG" -n "$ENV_NAME" --yes -o none || true
    fi
    echo "   creating in $ENV_REGION (takes 2-5 min)"
    az containerapp env create -g "$RG" -n "$ENV_NAME" -l "$ENV_REGION" -o none || true
    ENV_STATE=$(env_state)
    echo "   state: ${ENV_STATE:-not created}"
    [[ "$ENV_STATE" == "Succeeded" ]] && break
  done
  [[ "$ENV_STATE" == "Succeeded" ]] || { echo "Container Apps environment could not be provisioned. Set ENV_LOCATION=<region> and re-run."; exit 1; }
fi

app_exists() { az containerapp show -g "$RG" -n "$1" -o none 2>/dev/null; }

REG=(--registry-server "$ACR_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS")

echo ">> tuition-api (internal ingress: reachable only from inside the environment, i.e. via Kong)"
app_exists tuition-api && echo "   tuition-api already exists, skipping" || \
az containerapp create -g "$RG" -n tuition-api --environment "$ENV_NAME" "${REG[@]}" \
  --image "$ACR_SERVER/tuition-api:$TAG" --target-port 8000 --ingress internal \
  --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi \
  --secrets db-url="$DB_URL" jwt-secret="$JWT_SECRET" \
  --env-vars DATABASE_URL=secretref:db-url JWT_SECRET=secretref:jwt-secret SEED_DEMO_DATA=true -o none

API_FQDN=$(az containerapp show -g "$RG" -n tuition-api --query properties.configuration.ingress.fqdn -o tsv | tr -d '\r')

echo ">> mcp-server (external)"
app_exists mcp-server && echo "   mcp-server already exists, skipping" || \
az containerapp create -g "$RG" -n mcp-server --environment "$ENV_NAME" "${REG[@]}" \
  --image "$ACR_SERVER/mcp-server:$TAG" --target-port 8001 --ingress external \
  --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi -o none

echo ">> kong-gateway (external, single replica so the per-student counter is consistent)"
app_exists kong-gateway && echo "   kong-gateway already exists, skipping" || \
az containerapp create -g "$RG" -n kong-gateway --environment "$ENV_NAME" "${REG[@]}" \
  --image "$ACR_SERVER/kong-gateway:$TAG" --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1Gi \
  --secrets jwt-secret="$JWT_SECRET" \
  --env-vars TUITION_API_URL="https://$API_FQDN" JWT_SECRET=secretref:jwt-secret MOBILE_DAILY_LIMIT=3 -o none

GW=$(az containerapp show -g "$RG" -n kong-gateway --query properties.configuration.ingress.fqdn -o tsv | tr -d '\r')
MCP=$(az containerapp show -g "$RG" -n mcp-server --query properties.configuration.ingress.fqdn -o tsv | tr -d '\r')
cat <<EOF

================ DEPLOYED ================
API Gateway URL : https://$GW
Swagger UI      : https://$GW/api/docs
MCP endpoint    : https://$MCP/mcp
PostgreSQL      : $PG.postgres.database.azure.com (db: tuition)

Run the agent against the cloud:
  export GATEWAY_URL=https://$GW MCP_URL=https://$MCP/mcp GEMINI_API_KEY=...
  cd agent && python -m tuition_agent.cli --demo
Put these URLs into README.md -> "Deployed URLs".
EOF
