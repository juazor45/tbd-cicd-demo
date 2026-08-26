#!/usr/bin/env bash
# setup-azure-teams-bot.sh — Provisiona el bot de Microsoft Teams (DeployGo
# Assistant): Storage Account + Function App (Python, Consumption) + una
# User-Assigned Managed Identity + Azure Bot Service (tier F0, gratis) con
# el canal de Teams habilitado.
#
# Requiere que ya hayas corrido scripts/setup-azure.sh (usa el mismo resource
# group y la misma suscripción). Corre en Azure Cloud Shell igual que aquel.
#
# Uso:
#   ./setup-azure-teams-bot.sh <owner>/<repo>
#   ej: ./setup-azure-teams-bot.sh juazor45/tbd-cicd-demo
#
# Es idempotente: se puede volver a correr sin romper nada.
#
# Nota de seguridad: este script NO pide ni configura ANTHROPIC_API_KEY,
# JIRA_API_TOKEN ni ningún otro secreto de aplicación -- esos los cargás vos
# mismo al final, directo en Azure (Portal o az CLI local), sin pasárselos
# a Claude en ningún momento. Lo único que imprime al final son identificadores
# (no secretos), igual que setup-azure.sh.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Uso: $0 <owner>/<repo>   (ej: $0 juazor45/tbd-cicd-demo)"
  exit 1
fi

REPO_FULL="$1"
OWNER="${REPO_FULL%%/*}"
REPO="${REPO_FULL##*/}"

LOCATION="${LOCATION:-eastus2}"
RG="${RG:-rg-tbd-cicd-demo}"
HASH="$(echo -n "$REPO_FULL" | cksum | cut -d' ' -f1)"
STORAGE="${STORAGE:-sttbdbot${HASH}}"
FUNC_APP="${FUNC_APP:-func-tbd-teamsbot-${HASH}}"
BOT_NAME="${BOT_NAME:-bot-tbd-cicd-demo}"
BOT_UAMI_NAME="${BOT_UAMI_NAME:-uami-teams-bot}"

echo "=================================================================="
echo " Repositorio GitHub : $REPO_FULL"
echo " Resource Group     : $RG (debe existir -- corré setup-azure.sh primero)"
echo " Región             : $LOCATION"
echo "=================================================================="

if ! az group show -n "$RG" >/dev/null 2>&1; then
  echo "❌ No existe el resource group '$RG'. Corré scripts/setup-azure.sh primero."
  exit 1
fi

TENANT_ID="$(az account show --query tenantId -o tsv)"

echo "==> Storage account '$STORAGE' (requerido por el runtime de Azure Functions)..."
if ! az storage account show -g "$RG" -n "$STORAGE" >/dev/null 2>&1; then
  az storage account create -g "$RG" -n "$STORAGE" -l "$LOCATION" --sku Standard_LRS >/dev/null
fi

echo "==> Managed Identity '$BOT_UAMI_NAME' (identidad del bot -- sin client secret)..."
az identity create -g "$RG" -n "$BOT_UAMI_NAME" -l "$LOCATION" >/dev/null 2>&1 || true
UAMI_CLIENT_ID="$(az identity show -g "$RG" -n "$BOT_UAMI_NAME" --query clientId -o tsv)"
UAMI_ID="$(az identity show -g "$RG" -n "$BOT_UAMI_NAME" --query id -o tsv)"

echo "==> Function App '$FUNC_APP' (Python 3.11, Consumption)..."
if ! az functionapp show -g "$RG" -n "$FUNC_APP" >/dev/null 2>&1; then
  az functionapp create \
    -g "$RG" -n "$FUNC_APP" \
    --consumption-plan-location "$LOCATION" \
    --runtime python --runtime-version 3.11 --functions-version 4 \
    --os-type Linux \
    --storage-account "$STORAGE" >/dev/null
fi

echo "==> Asignando la Managed Identity a la Function App..."
az functionapp identity assign -g "$RG" -n "$FUNC_APP" --identities "$UAMI_ID" >/dev/null

echo "==> Configurando MicrosoftAppType/Id/TenantId (auth del bot, sin secretos)..."
az functionapp config appsettings set -g "$RG" -n "$FUNC_APP" --settings \
  "MicrosoftAppType=UserAssignedMSI" \
  "MicrosoftAppId=$UAMI_CLIENT_ID" \
  "MicrosoftAppTenantId=$TENANT_ID" >/dev/null

FUNC_HOST="$(az functionapp show -g "$RG" -n "$FUNC_APP" --query defaultHostName -o tsv)"
FUNC_URL="https://${FUNC_HOST}/api/messages"

echo "==> Azure Bot '$BOT_NAME' (tier F0, gratis)..."
if ! az bot show -g "$RG" -n "$BOT_NAME" >/dev/null 2>&1; then
  az bot create \
    -g "$RG" -n "$BOT_NAME" \
    --app-type UserAssignedMSI \
    --appid "$UAMI_CLIENT_ID" \
    --tenant-id "$TENANT_ID" \
    --msi-resource-id "$UAMI_ID" \
    --endpoint "$FUNC_URL" \
    --sku F0 >/dev/null
else
  az bot update -g "$RG" -n "$BOT_NAME" --endpoint "$FUNC_URL" >/dev/null
fi

echo "==> Habilitando el canal de Microsoft Teams..."
az bot msteams create -g "$RG" -n "$BOT_NAME" >/dev/null 2>&1 || echo "    (ya estaba habilitado, o falló -- revisar a mano en el Portal si hace falta)"

echo ""
echo "=================================================================="
echo " LISTO. Pegá esto en el chat con Claude (no son secretos, son IDs):"
echo "=================================================================="
echo "AZURE_FUNCTIONAPP_NAME = $FUNC_APP"
echo "MicrosoftAppId (UAMI)  = $UAMI_CLIENT_ID"
echo "MicrosoftAppTenantId   = $TENANT_ID"
echo "Messaging endpoint     = $FUNC_URL"
echo "=================================================================="
echo ""
echo "MicrosoftAppType/Id/TenantId ya quedaron configurados en la Function"
echo "App (sin secretos, vía Managed Identity). ANTHROPIC_API_KEY, JIRA_*"
echo "los toma el workflow 'Teams Bot - Deploy' de los mismos Secrets que"
echo "ya tenés en el repo para spec-review.yml/cicd-cert.yml -- no hace"
echo "falta cargarlos de nuevo a mano."
echo ""
echo "Lo único que falta antes de correr ese workflow: agregar estas dos"
echo "Variables nuevas en GitHub (Settings > Secrets and variables > Actions"
echo "> Variables), además de AZURE_FUNCTIONAPP_NAME de arriba:"
echo "  JIRA_PROJECT_KEY        = (la key de tu proyecto, ej. SCRUM)"
echo "  TEAMS_TICKET_CHANNEL_ID = (el ID del canal de Teams, ver abajo)"
echo ""
echo "El ID del canal de Teams se saca desde Teams: los 3 puntos del canal"
echo "-> Obtener vínculo al canal -> el ID largo que empieza con 19:."
echo "=================================================================="
