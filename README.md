# tbd-cicd-demo

Laboratorio funcional de un proceso de entrega de software completo: **Trunk-Based Development**, pipelines CI/CD con gestión de cambios automatizada contra Jira, y un **asistente de IA** que responde en lenguaje natural en qué punto está cada release.

---

## Qué contiene

| Componente | Descripción |
|---|---|
| **Estrategia de ramas** | Trunk-Based Development sobre `main`, con ramas de vida corta y PR obligatorio |
| **Microservicio** | `ms-exchange-rate` — API de tipos de cambio en Quarkus 3 + Java 17 + Maven |
| **Pipelines** | 4 workflows de GitHub Actions: validación de rama, validación de PR, CI/CD a dev y CI/CD a cert |
| **Change management** | El tablero de Jira avanza automáticamente conforme avanza el pipeline |
| **Asistente** | Agente de IA con 4 herramientas, accesible desde Slack, web, terminal y Actions |

---

## 1. Estrategia de ramas: Trunk-Based Development

```
main (trunk) ──●──●──●──●──●──●──●──►  siempre desplegable
                \    /  \      /
   feature/*      ●──●    \    /        ramas cortas (< 2 días)
   fix/*                   ●──●         merge vía Pull Request
```

### Reglas

| Regla | Detalle |
|---|---|
| Trunk único | `main` es la única rama de larga vida y siempre está desplegable |
| Ramas cortas | `feature/*` y `fix/*` nacen de `main` y viven 1–2 días máximo |
| Convención de nombres | **Debe incluir la key del ticket**: `feature/SCRUM-11-descripcion` |
| Integración frecuente | Merge a `main` al menos una vez al día, vía Pull Request |
| PR pequeños | Cambios revisables, con el check `CI` obligatorio en verde |
| Sin ramas de release | Se despliega desde `main` con versión/tag; los hotfix se corrigen en `main` |
| Feature flags | La funcionalidad incompleta se integra apagada, no en ramas largas |

### Flujo de trabajo

```bash
git checkout main && git pull
git checkout -b feature/SCRUM-11-nueva-api   # dispara Jira-Branch
# ... commits pequeños ...
git push -u origin feature/SCRUM-11-nueva-api
```

Después: se ejecuta `CICD-DEV` desde la rama para desplegar y probar en desarrollo (queda el status `validado-en-dev` sobre el commit), se abre el Pull Request, se aprueba, y se hace squash merge a `main`. La rama se borra automáticamente. La promoción a certificación se lanza después, ya desde `main`.

---

## 2. El microservicio: `ms-exchange-rate`

API REST de tipos de cambio contra el Sol peruano, con datos en memoria (sin base de datos, para no complicar el pipeline).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/exchange-rates` | Lista todas las divisas (USD, EUR, CLP) |
| GET | `/api/v1/exchange-rates/{currency}` | Tipo de cambio de una divisa (404 si no existe) |
| GET | `/api/v1/exchange-rates/{currency}/convert?amount=100` | Convierte un monto a PEN |
| GET | `/q/health/live` · `/q/health/ready` | Probes para Kubernetes |

```bash
mvn quarkus:dev      # modo dev con live reload
mvn verify           # tests + coverage (JaCoCo)
mvn package          # fast-jar en target/quarkus-app/
docker build -t ms-exchange-rate:local .
```

---

## 3. Pipelines

| Workflow | Se dispara | Qué hace |
|---|---|---|
| `jira-branch.yml` | Al crear una rama | Extrae la key del nombre, verifica el ticket en Jira y lo mueve a **Construcción Doing** |
| `ci-pr.yml` | En cada PR hacia `main` | `mvn verify` real — es el check obligatorio del ruleset |
| `cicd-dev.yml` | Manual, desde cualquier rama | CI completo + deploy a dev con smoke test → **Construcción Done**. Al terminar, marca el commit con el status `validado-en-dev` |
| `cicd-cert.yml` | Manual, **solo desde `main`** | Valida origen y ticket, CI con policies estrictas, aprobación manual y deploy → **Congelamiento** y **QA Testing Doing** |

**Etapas de CI**: Prepare → Get Secrets (Vault) → Build & Tests (Maven) → SonarQube → Fortify → Publish Artifact → Component Test → SCA Xray

**Etapas de CD**: Prepare → Build & Push Registry → Set Runners → Deploy to Kubernetes → Smoke Test

> Los análisis de seguridad/calidad y el despliegue a Kubernetes están **simulados**. El build de Maven, la construcción de la imagen Docker y el smoke test son reales.

Los workflows manuales llevan el ticket en el título del run (`run-name`), lo que permite correlacionar cada ejecución con su release.

### Controles del flujo

| Control | Qué impide |
|---|---|
| Check `CI` requerido en el PR | Integrar código que no compila o con tests rotos |
| Check `validado-en-dev` requerido | Mergear un commit que no fue desplegado y probado en desarrollo. El status se asocia al SHA: si alguien empuja un commit nuevo al PR, el merge se bloquea hasta volver a validar |
| Guard de rama en `cicd-cert.yml` | Certificar código que no pasó por Pull Request (el pipeline aborta si no corre desde `main`) |
| Estado del ticket en `cicd-cert.yml` | Promover a certificación un cambio que no completó la fase de construcción |
| Approval del environment `cert` | Desplegar en certificación sin aprobación humana |

Como refuerzo, el environment `cert` restringe los despliegues a la rama `main`, de modo que el control no depende únicamente del YAML.

---

## 4. Gestión de cambios (Jira)

Proyecto Scrum con seis estados que el pipeline recorre automáticamente:

```
Construcción Doing → Construcción Done → Congelamiento Doing
                  → Congelamiento Done → QA Testing Doing → QA Testing Done
```

| Momento | Transición |
|---|---|
| Se crea la rama | → Construcción Doing |
| CI de dev exitoso | → Construcción Done |
| Inicia CICD-CERT | → Congelamiento Doing |
| CI de cert completo | → Congelamiento Done |
| Deploy en cert | → QA Testing Doing |
| QA certifica (manual) | → QA Testing Done |

`CICD-CERT` **aborta** si el ticket no está en el estado requerido: el proceso protege al proceso.

---

## 5. El asistente

Agente de IA que cruza tres fuentes para responder *¿dónde está mi release y qué sigue?*

**Herramientas** (`scripts/tools.py`):

- `consultar_jira` — estado del ticket de cambio
- `consultar_pipelines` — ejecuciones de los workflows, filtrables por ticket
- `detalle_ejecucion` — jobs y steps: dónde falló, qué ejecuta o qué espera aprobación
- `consultar_proceso` — el template con fases, criterios y siguiente paso

**Interfaces disponibles:**

| Archivo | Interfaz | Uso |
|---|---|---|
| `scripts/slack_bot.py` | Slack (Socket Mode) | `/release SCRUM-11` o `@DeployGo ¿por qué falló?` |
| `scripts/api.py` + `static/` | Web (FastAPI) | `uvicorn api:app --port 8000` |
| `scripts/assistant.py` | Terminal | `python assistant.py "¿en qué va SCRUM-11?"` |
| `.github/workflows/consulta-estado.yml` | GitHub Actions | Run workflow → reporte en el Summary |

El conocimiento del proceso vive en `scripts/process-template.yml`, un archivo editable. **Cuando el proceso cambia, se edita ese archivo — no el código.**

---

## Configuración

### Secrets del repositorio (Actions)

`JIRA_BASE_URL` · `JIRA_EMAIL` · `JIRA_API_TOKEN` · `ANTHROPIC_API_KEY`

### Variables para el asistente (local o Codespaces)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export JIRA_BASE_URL="https://tusitio.atlassian.net"
export JIRA_EMAIL="tu-correo"
export JIRA_API_TOKEN="tu-token"
export GITHUB_REPO="$GITHUB_REPOSITORY"
export SLACK_BOT_TOKEN="xoxb-..."   # solo para el bot
export SLACK_APP_TOKEN="xapp-..."   # solo para el bot
```

### Protección del trunk

En **Settings → Rules → Rulesets**, sobre `main`: PR obligatorio, checks requeridos `CI` y `validado-en-dev`, bloqueo de force push, solo squash merge y borrado automático de ramas. El script `scripts/setup-repo.sh` lo aplica con `gh` CLI.

En **Settings → Environments**, crear `dev` y `cert`; en `cert`, activar *Required reviewers* para el approval gate y restringir *Deployment branches* a `main`.

---

## Estructura

```
.
├── .github/workflows/     jira-branch · ci-pr · cicd-dev · cicd-cert · consulta-estado
├── src/                   microservicio Quarkus (main y test)
├── scripts/               asistente: tools · slack_bot · assistant · api · process-template
├── static/                interfaz web del asistente
├── docs/                  estrategia de ramas
├── pom.xml
└── Dockerfile
```

---

## Alcance

Prueba de concepto para validar el flujo completo y la utilidad del asistente. No es un entorno productivo: no hay cluster real, los gates de seguridad son simulados y las credenciales se gestionan con secrets de repositorio.
