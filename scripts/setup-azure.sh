#!/usr/bin/env bash
# setup-azure.sh — Provisiona en Azure todo lo necesario para desplegar
# ms-exchange-rate en Azure Container Apps (plan Consumption), y deja
# configurado el login sin secretos (OIDC/federated credentials) para que
# GitHub Actions pueda desplegar sin guardar ninguna clave de Azure.
#
# Pensado para correr en Azure Cloud Shell (https://shell.azure.com) --
# ya viene con az CLI instalado y autenticado con tu cuenta. También corre
# en cualquier máquina con az CLI + `az login` ya hecho.
#
# Es idempotente: se puede volver a correr sin romper nada si ya existe
# parte de la infraestructura (por ejemplo, para levantar el mismo
# laboratorio en otra laptop/suscripción).
#
# Uso:
#   ./setup-azure.sh <owner>/<repo>
#   ej: ./setup-azure.sh juazor45/tbd-cicd-demo
#
# Al final imprime los valores que hay que pegar como Secrets/Variables
# en GitHub (Settings > Secrets and variables > Actions). Ninguno de esos
# valores es una contraseña ni una clave secreta -- son identificadores
# (client id, tenant id, subscription id), por eso es seguro copiarlos y
# pegarlos en un chat si hace falta.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Uso: $0 <owner>/<repo>   (ej: $0 juazor45/tbd-cicd-demo)"
  exit 1
fi

REPO_FULL="$1"          # ej. juazor45/tbd-cicd-demo
OWNER="${REPO_FULL%%/*}"
REPO="${REPO_FULL##*/}"

# ---- Variables configurables (podés sobreescribirlas con env vars) ----
LOCATION="${LOCATION:-eastus2}"
RG="${RG:-rg-tbd-cicd-demo}"
LAW="${LAW:-law-tbd-cicd-demo}"
ACA_ENV="${ACA_ENV:-env-tbd-cicd-demo}"
APP_DEV="${APP_DEV:-ms-exchange-rate-dev}"
APP_CERT="${APP_CERT:-ms-exchange-rate-cert}"
AAD_APP_NAME="${AAD_APP_NAME:-github-oidc-$REPO}"
PLACEHOLDER_IMAGE="mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"

echo "=================================================================="
echo " Repositorio GitHub : $REPO_FULL"
echo " Región             : $LOCATION"
echo " Resource Group     : $RG"
echo "=================================================================="

echo "==> Extensión containerapp de az CLI..."
az extension add --name containerapp --upgrade -y >/dev/null 2>&1 || true

echo "==> Registrando resource providers necesarios..."
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait

SUB_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
echo "==> Suscripción activa: $SUB_ID (tenant $TENANT_ID)"

# ---- Resource Group ----
echo "==> Resource group '$RG'..."
az group create --name "$RG" --location "$LOCATION" >/dev/null

# ---- Log Analytics (requerido por el entorno de Container Apps) ----
echo "==> Log Analytics workspace '$LAW'..."
if ! az monitor log-analytics workspace show -g "$RG" -n "$LAW" >/dev/null 2>&1; then
  az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOCATION" >/dev/null
fi
LAW_CLIENT_ID="$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)"
LAW_CLIENT_SECRET="$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" --query primarySharedKey -o tsv)"

# ---- Entorno de Container Apps (plan Consumption -- el que escala a 0) ----
echo "==> Container Apps Environment '$ACA_ENV' (Consumption)..."
if ! az containerapp env show -g "$RG" -n "$ACA_ENV" >/dev/null 2>&1; then
  az containerapp env create \
    -g "$RG" -n "$ACA_ENV" -l "$LOCATION" \
    --logs-workspace-id "$LAW_CLIENT_ID" \
    --logs-workspace-key "$LAW_CLIENT_SECRET" >/dev/null
fi

# ---- Container Apps: una para dev, otra para cert ----
# Arrancan con una imagen placeholder pública -- el primer deploy real del
# pipeline (cicd-dev.yml / cicd-cert.yml) la reemplaza por la imagen real
# de ms-exchange-rate publicada en GHCR.
crear_container_app () {
  local NOMBRE="$1"
  echo "==> Container App '$NOMBRE'..."
  if az containerapp show -g "$RG" -n "$NOMBRE" >/dev/null 2>&1; then
    echo "    ya existe, no se toca."
    return
  fi
  az containerapp create \
    -g "$RG" -n "$NOMBRE" \
    --environment "$ACA_ENV" \
    --image "$PLACEHOLDER_IMAGE" \
    --target-port 80 \
    --ingress external \
    --min-replicas 0 \
    --max-replicas 1 \
    --cpu 0.25 --memory 0.5Gi >/dev/null
}
crear_container_app "$APP_DEV"
crear_container_app "$APP_CERT"

URL_DEV="$(az containerapp show -g "$RG" -n "$APP_DEV" --query properties.configuration.ingress.fqdn -o tsv)"
URL_CERT="$(az containerapp show -g "$RG" -n "$APP_CERT" --query properties.configuration.ingress.fqdn -o tsv)"

# ---- App Registration + federated credentials (login sin secretos) ----
echo "==> App Registration '$AAD_APP_NAME' para login OIDC desde GitHub Actions..."
APP_ID="$(az ad app list --display-name "$AAD_APP_NAME" --query "[0].appId" -o tsv)"
if [ -z "$APP_ID" ]; then
  APP_ID="$(az ad app create --display-name "$AAD_APP_NAME" --query appId -o tsv)"
fi
# Service principal asociado (si ya existe, az ad sp create falla -- se ignora)
az ad sp create --id "$APP_ID" >/dev/null 2>&1 || true

echo "==> Dando permiso Contributor sobre '$RG' (acotado solo a este resource group)..."
az role assignment create \
  --assignee "$APP_ID" \
  --role "Contributor" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$RG" >/dev/null 2>&1 || \
  echo "    (el permiso ya existía)"

# Desde julio 2026 GitHub manda por defecto un subject "inmutable" que incluye
# el ID del owner y del repo (no solo el nombre), para blindar contra
# reutilización de nombres si se renombra/transfiere el repo. Lo detectamos
# acá para armar el subject correcto sin que haga falta tocar nada a mano.
echo "==> Detectando formato de subject OIDC (owner/repo IDs)..."
OWNER_ID="$(curl -s "https://api.github.com/users/$OWNER" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
REPO_ID="$(curl -s "https://api.github.com/repos/$REPO_FULL" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
if [ -n "$OWNER_ID" ] && [ -n "$REPO_ID" ]; then
  SUBJECT_OWNER_REPO="${OWNER}@${OWNER_ID}/${REPO}@${REPO_ID}"
  echo "    usando subject inmutable: repo:${SUBJECT_OWNER_REPO}:..."
else
  SUBJECT_OWNER_REPO="${REPO_FULL}"
  echo "    ⚠️  no pude resolver los IDs vía api.github.com -- uso el formato clásico (repo:${REPO_FULL}:...)."
  echo "        Si tu repo ya usa subjects inmutables, esto va a fallar en el login; volvé a correr el script."
fi

# Siempre borra-y-crea (en vez de "si existe, no tocar") para que re-correr
# el script también sirva para corregir un subject desactualizado.
crear_federated_credential () {
  local NOMBRE="$1"
  local SUBJECT="$2"
  local TMP_JSON
  TMP_JSON="$(mktemp)"
  cat > "$TMP_JSON" << EOF
{
  "name": "$NOMBRE",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$SUBJECT",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
  az ad app federated-credential delete --id "$APP_ID" --federated-credential-id "$NOMBRE" >/dev/null 2>&1 || true
  az ad app federated-credential create --id "$APP_ID" --parameters "$TMP_JSON" >/dev/null
  echo "    credential '$NOMBRE' -> subject: $SUBJECT"
  rm -f "$TMP_JSON"
}

echo "==> Federated credentials (para que GitHub Actions se loguee sin secretos)..."
crear_federated_credential "gh-env-dev"  "repo:${SUBJECT_OWNER_REPO}:environment:dev"
crear_federated_credential "gh-env-cert" "repo:${SUBJECT_OWNER_REPO}:environment:cert"
crear_federated_credential "gh-main"     "repo:${SUBJECT_OWNER_REPO}:ref:refs/heads/main"

echo ""
echo "=================================================================="
echo " LISTO. Pegá esto en el chat con Claude (no son secretos, son IDs):"
echo "=================================================================="
echo "AZURE_CLIENT_ID       = $APP_ID"
echo "AZURE_TENANT_ID       = $TENANT_ID"
echo "AZURE_SUBSCRIPTION_ID = $SUB_ID"
echo "AZURE_RESOURCE_GROUP  = $RG"
echo "AZURE_ACA_ENV         = $ACA_ENV"
echo "AZURE_APP_DEV         = $APP_DEV"
echo "AZURE_APP_CERT        = $APP_CERT"
echo "URL dev  (aún con placeholder) = https://$URL_DEV"
echo "URL cert (aún con placeholder) = https://$URL_CERT"
echo "=================================================================="
