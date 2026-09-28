#!/usr/bin/env bash
# setup-azure-static-site.sh — Provisiona una Storage Account en el mismo
# Resource Group que ya usan ms-exchange-rate-dev/cert y el bot de Slack, y
# la habilita como static website para servir web/index.html (el probador
# manual de ms-exchange-rate).
#
# Usa Blob Storage estatico (no un Container App): es la opcion mas barata
# para un sitio 100% estatico (HTML/CSS/JS sin backend propio) -- cobra por
# almacenamiento y transferencia, sin computo corriendo en segundo plano, a
# diferencia de un Container App que aunque escale a 0 sigue siendo un
# recurso de computo.
#
# Requiere que ya hayas corrido scripts/setup-azure.sh (usa el mismo
# resource group y aprovecha el mismo permiso Contributor que ya tiene la
# identidad de GitHub Actions sobre ese resource group -- no hace falta
# ningun rol nuevo). Corre en Azure Cloud Shell igual que aquel.
#
# Uso:
#   ./setup-azure-static-site.sh
#   STORAGE_ACCOUNT=miNombreUnico ./setup-azure-static-site.sh   # si el default ya esta tomado
#
# El nombre de una Storage Account es GLOBAL en toda Azure (no solo en tu
# suscripcion): minusculas y numeros, 3-24 caracteres, sin guiones. Si
# az storage account create falla por "el nombre ya existe", volve a
# correr con otro STORAGE_ACCOUNT.
#
# Es idempotente: se puede volver a correr sin romper nada.

set -euo pipefail

RG="${RG:-rg-tbd-cicd-demo}"
LOCATION="${LOCATION:-eastus2}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-sttbdcicddemoweb}"

echo "=================================================================="
echo " Resource Group     : $RG (debe existir -- corré setup-azure.sh primero)"
echo " Storage Account     : $STORAGE_ACCOUNT"
echo " Región              : $LOCATION"
echo "=================================================================="

if ! az group show -n "$RG" >/dev/null 2>&1; then
  echo "❌ No existe el resource group '$RG'. Corré scripts/setup-azure.sh primero."
  exit 1
fi

echo "==> Storage Account '$STORAGE_ACCOUNT' (Standard_LRS, StorageV2)..."
if ! az storage account show -g "$RG" -n "$STORAGE_ACCOUNT" >/dev/null 2>&1; then
  az storage account create \
    -g "$RG" -n "$STORAGE_ACCOUNT" -l "$LOCATION" \
    --sku Standard_LRS --kind StorageV2 \
    --allow-blob-public-access true \
    --min-tls-version TLS1_2 \
    >/dev/null
  echo "    creada."
else
  echo "    ya existe, no se toca."
fi

STORAGE_KEY="$(az storage account keys list -g "$RG" -n "$STORAGE_ACCOUNT" --query '[0].value' -o tsv)"

echo "==> Habilitando static website (index.html como documento de error tambien -- es una SPA de una sola pagina)..."
az storage blob service-properties update \
  --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" \
  --static-website --index-document index.html --404-document index.html \
  >/dev/null

echo "==> Subiendo el contenido inicial de web/ ..."
az storage blob upload-batch \
  --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" \
  -d '$web' -s web/ --overwrite \
  >/dev/null

SITE_URL="$(az storage account show -g "$RG" -n "$STORAGE_ACCOUNT" --query "primaryEndpoints.web" -o tsv)"

echo ""
echo "=================================================================="
echo " LISTO. Pegá esto en el chat con Claude (no es secreto, es un nombre):"
echo "=================================================================="
echo "AZURE_STORAGE_ACCOUNT = $STORAGE_ACCOUNT"
echo "=================================================================="
echo ""
echo "El probador ya está publicado en:"
echo "  $SITE_URL"
echo ""
echo "Antes de que el workflow 'Static Site - Deploy' pueda actualizarlo"
echo "automáticamente, agregá en GitHub (Settings > Secrets and variables >"
echo "Actions > Variables):"
echo "  AZURE_STORAGE_ACCOUNT = $STORAGE_ACCOUNT"
echo ""
echo "No hace falta ningún rol nuevo: la misma identidad OIDC de GitHub"
echo "Actions que ya tiene Contributor sobre '$RG' (de setup-azure.sh) puede"
echo "leer la clave de esta Storage Account porque está en el mismo"
echo "resource group."
echo "=================================================================="
