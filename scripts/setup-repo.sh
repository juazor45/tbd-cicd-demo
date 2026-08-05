#!/usr/bin/env bash
# Configura el repositorio para Trunk-Based Development usando GitHub CLI (gh).
# Uso: ./scripts/setup-repo.sh <owner> <repo>
set -euo pipefail

OWNER="${1:?Uso: $0 <owner> <repo>}"
REPO="${2:?Uso: $0 <owner> <repo>}"

echo "==> Configurando merge strategy (solo squash + auto-delete branches)..."
gh api -X PATCH "repos/$OWNER/$REPO" \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -F allow_squash_merge=true \
  -F delete_branch_on_merge=true

echo "==> Creando branch protection en main..."
gh api -X PUT "repos/$OWNER/$REPO/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": []
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo "==> Creando environments dev y cert..."
gh api -X PUT "repos/$OWNER/$REPO/environments/dev" >/dev/null
gh api -X PUT "repos/$OWNER/$REPO/environments/cert" >/dev/null
echo "    (Agrega 'Required reviewers' al environment cert desde la UI: Settings > Environments > cert)"

echo "✅ Repo configurado para Trunk-Based Development"
