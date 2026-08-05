# tbd-cicd-demo

Repositorio de prueba con estrategia **Trunk-Based Development (TBD)** y pipelines CI/CD mockeados (`workflow_dispatch`) para los ambientes **DEV** y **CERT**.

## Estrategia de ramas: Trunk-Based Development

```
main (trunk) ──●──●──●──●──●──●──●──●──► siempre desplegable
                \    /  \      /
    feature/x    ●──●    \    /          ramas cortas (< 2 días)
    fix/y                 ●──●           merge vía Pull Request
```

### Reglas

| Regla | Detalle |
|---|---|
| Trunk único | `main` es la única rama de larga vida y siempre está en estado desplegable |
| Ramas cortas | `feature/*`, `fix/*` viven máximo 1–2 días y nacen desde `main` |
| Integración frecuente | Merge a `main` mínimo 1 vez al día vía Pull Request |
| PR pequeños | Cambios pequeños y revisables (< 400 líneas idealmente) |
| Sin ramas de release largas | Se despliega desde `main` usando tags/versiones; si se necesita hotfix, se hace en `main` y se cherry-pick al tag |
| Feature flags | Funcionalidad incompleta se integra apagada detrás de un flag, no en ramas largas |

### Convención de nombres

- `feature/JIRA-123-descripcion-corta`
- `fix/JIRA-456-descripcion-corta`

### Flujo de trabajo

```bash
git checkout main && git pull
git checkout -b feature/JIRA-123-nueva-api
# ... commits pequeños ...
git push -u origin feature/JIRA-123-nueva-api
# Abrir PR → checks (CI) → review → squash merge a main → borrar rama
```

## Pipelines (workflow_dispatch)

Ambos pipelines se ejecutan manualmente desde **Actions → seleccionar workflow → Run workflow**.

### `CICD-DEV` (.github/workflows/cicd-dev.yml)

| Fase | Steps |
|---|---|
| **CI** | Prepare → Get Secrets (HashiCorp Vault) → SonarQube → Fortify → Publish Artifact → Component Test (mock server) → SCA Xray |
| **CD** | Prepare → Build & Push Registry → Set Runners → Deploy to Kubernetes Cluster |

### `CICD-CERT` (.github/workflows/cicd-cert.yml)

Mismos steps, con diferencias propias de certificación:

- Requiere input `change_ticket` (ticket de cambio Jira).
- Component tests contra **dependencias reales** (no mock server).
- Policies de seguridad más estrictas (sin waivers de críticas/altas).
- El job de CD usa `environment: cert` → permite configurar **required reviewers** (approval gate) en *Settings → Environments → cert*.

> Todos los steps están **mockeados** con `echo` para fines de prueba/demostración. Para hacerlos reales, reemplazar cada bloque `run` por las actions/CLIs correspondientes (sonarsource/sonarqube-scan-action, fortify, jfrog CLI, hashicorp/vault-action, azure/k8s-deploy, etc.).

## Configuración recomendada del repo (para reforzar TBD)

1. **Settings → Branches → Branch protection rule** para `main`:
   - ✅ Require a pull request before merging (1+ approval)
   - ✅ Require status checks to pass (job `ci`)
   - ✅ Require branches to be up to date
   - ✅ Block force pushes / deletions
2. **Settings → General**: habilitar solo *Squash merging* y *Automatically delete head branches*.
3. **Settings → Environments**: crear `dev` y `cert`; en `cert` agregar *Required reviewers*.

O ejecuta el script incluido:

```bash
./scripts/setup-repo.sh <owner> <repo>
```

## Estructura

```
.
├── .github/workflows/
│   ├── cicd-dev.yml
│   └── cicd-cert.yml
├── docs/
│   └── branching-strategy.md
├── scripts/
│   └── setup-repo.sh
└── README.md
```

