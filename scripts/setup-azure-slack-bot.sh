#!/usr/bin/env bash
# setup-azure-slack-bot.sh — Provisiona el Container App que corre el bot de
# Slack (DeployGo Assistant): un Container App mas en el mismo Resource
# Group y Container Apps Environment que ya usan ms-exchange-rate-dev/cert,
# SIN ingress (Socket Mode abre una conexion SALIENTE hacia Slack, no
# necesita puerto publico) y con min-replicas=1 -- a diferencia de esas dos,
# este no puede escalar a 0: tiene que estar siempre corriendo para
# mantener la conexion con Slack.
#
# Requiere que ya hayas corrido scripts/setup-azure.sh (usa el mismo
# resource group y el mismo Container Apps Environment). Corre en Azure
# Cloud Shell igual que aquel.
#
# Uso:
#   export SLACK_BOT_TOKEN="xoxb-..."
#   export SLACK_APP_TOKEN="xapp-..."
#   ./setup-azure-slack-bot.sh <owner>/<repo>
#   ej: ./setup-azure-slack-bot.sh juazor45/tbd-cicd-demo
#
# Es idempotente: se puede volver a correr (por ejemplo, para rotar los
# tokens de Slack) sin romper nada.
#
# Nota de seguridad: este script SOLO carga los dos secretos de Slack, y los
# toma de tus propias variables de entorno -- nunca hardcodeados en el
# script ni pegados en un chat. ANTHROPIC_API_KEY, JIRA_BASE_URL, JIRA_EMAIL
# y JIRA_API_TOKEN los configura despues el workflow "Slack Bot - Deploy",
# reusando los mismos Secrets que ya existen en el repo para
# spec-review.yml/cicd-cert.yml -- no hace falta cargarlos de nuevo aca.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Uso: $0 <owner>/<repo>   (ej: $0 juazor45/tbd-cicd-demo)"
  exit 1
fi
if [ -z "${SLACK_BOT_TOKEN:-}" ] || [ -z "${SLACK_APP_TOKEN:-}" ]; then
  echo "❌ Exportá SLACK_BOT_TOKEN y SLACK_APP_TOKEN antes de correr este script:"
  echo "   export SLACK_BOT_TOKEN=\"xoxb-...\""
  echo "   export SLACK_APP_TOKEN=\"xapp-...\""
  exit 1
fi

REPO_FULL="$1"

RG="${RG:-rg-tbd-cicd-demo}"
ENV_NAME="${ENV_NAME:-env-tbd-cicd-demo}"
APP_NAME="${APP_NAME:-deploygo-slack-bot}"

echo "=================================================================="
echo " Repositorio GitHub : $REPO_FULL"
echo " Resource Group     : $RG (debe existir -- corré setup-azure.sh primero)"
echo " Environment        : $ENV_NAME (debe existir -- lo crea setup-azure.sh)"
echo " Container App      : $APP_NAME"
echo "=================================================================="

if ! az group show -n "$RG" >/dev/null 2>&1; then
  echo "❌ No existe el resource group '$RG'. Corré scripts/setup-azure.sh primero."
  exit 1
fi

az extension add --name containerapp --upgrade -y >/dev/null 2>&1

if ! az containerapp env show -g "$RG" -n "$ENV_NAME" >/dev/null 2>&1; then
  echo "❌ No existe el Container Apps Environment '$ENV_NAME'. Corré scripts/setup-azure.sh primero."
  exit 1
fi

echo "==> Container App '$APP_NAME' (sin ingress -- Socket Mode es 100% saliente)..."
if ! az containerapp show -g "$RG" -n "$APP_NAME" >/dev/null 2>&1; then
  az containerapp create \
    -g "$RG" -n "$APP_NAME" \
    --environment "$ENV_NAME" \
    --image mcr.microsoft.com/k8se/quickstart:latest \
    --min-replicas 1 --max-replicas 1 \
    --secrets "slack-bot-token=$SLACK_BOT_TOKEN" "slack-app-token=$SLACK_APP_TOKEN" \
    --env-vars \
      "SLACK_BOT_TOKEN=secretref:slack-bot-token" \
      "SLACK_APP_TOKEN=secretref:slack-app-token" \
      "GITHUB_REPO=$REPO_FULL" \
    >/dev/null
  echo "    Creado (con la imagen placeholder de Microsoft -- 'Slack Bot - Deploy' la reemplaza con la real)."
else
  echo "    Ya existe -- actualizando solo los secretos de Slack (por si rotaste los tokens)."
  az containerapp secret set -g "$RG" -n "$APP_NAME" \
    --secrets "slack-bot-token=$SLACK_BOT_TOKEN" "slack-app-token=$SLACK_APP_TOKEN" >/dev/null
fi

echo ""
echo "=================================================================="
echo " LISTO. Pegá esto en el chat con Claude (no es secreto, es un nombre):"
echo "=================================================================="
echo "AZURE_APP_SLACK = $APP_NAME"
echo "=================================================================="
echo ""
echo "Los tokens de Slack ya quedaron cargados como secretos del Container"
echo "App -- nunca pasaron por GitHub ni por este chat. ANTHROPIC_API_KEY,"
echo "JIRA_BASE_URL, JIRA_EMAIL y JIRA_API_TOKEN los configura el workflow"
echo "'Slack Bot - Deploy' reusando los Secrets que ya tenés en el repo --"
echo "no hace falta cargarlos de nuevo."
echo ""
echo "Antes de correr ese workflow, agregá en GitHub (Settings > Secrets and"
echo "variables > Actions > Variables):"
echo "  AZURE_APP_SLACK          = $APP_NAME"
echo "  JIRA_TICKET_CHANNEL_ID   = (el ID del canal de Slack para /crear-ticket, opcional)"
echo ""
echo "Este Container App corre con min-replicas=1 (no escala a 0): a"
echo "diferencia de ms-exchange-rate-dev/cert, tiene que estar siempre"
echo "prendido para mantener la conexión Socket Mode con Slack -- es costo"
echo "continuo, aunque de una sola réplica chica."
echo "=================================================================="
