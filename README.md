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
| **Specs** | Contrato del cambio por ticket (`specs/<TICKET>.yml`): qué debe cambiar, qué no debe tocarse, y evidencia requerida |
| **Dashboard de auditoría** | `docs/dashboard.md` (tabla) + `docs/dashboard.html` (panel visual con buscador): por cada ticket, spec + estado en Jira + fase del proceso + pipelines + PRs, todo enlazado |
| **Asistente** | Agente de IA con 5 herramientas, accesible desde Slack, terminal y Actions |

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

### Specs: el contrato del cambio

Al crear la rama, copia `specs/TEMPLATE.yml` a `specs/<TICKET>.yml` (ver `specs/SCRUM-14.yml` como ejemplo real) y declara:

| Campo | Qué significa |
|---|---|
| `cambios_permitidos` | Rutas (glob) que el PR puede tocar |
| `cambios_prohibidos` | Rutas que NO debe tocar bajo ningún motivo, para este ticket |
| `contrato` | Qué comportamiento de API debe preservarse o agregarse |
| `evidencia_requerida` | Qué prueba que el trabajo quedó hecho |

Dos capas de validación en cada PR, ambas en **modo advisorio** (informan, no bloquean el merge todavía):

- `ci-pr.yml` → `scripts/validate_spec.py`: compara los **archivos** reales del PR contra `cambios_permitidos`/`cambios_prohibidos` (matching de rutas, determinístico).
- `spec-review.yml` → `scripts/review_agent.py`: le pasa a Claude el **diff real** (el contenido, no solo los nombres de archivo) junto con el spec, y publica un comentario en el PR con un veredicto y hallazgos — por ejemplo, si el contrato dice que un formato de respuesta no debe cambiar pero el diff sí lo toca, o si falta un test que el spec pide como evidencia. Se omite en silencio si la rama no tiene ticket o el ticket no tiene spec (PRs de Dependabot, chores, etc.) — pero si hay algo que revisar y falla por un error real (por ejemplo, falta el secret `ANTHROPIC_API_KEY`), el check queda en rojo a propósito, para que se note que el review no se hizo. Como no es un check requerido, nunca bloquea el merge.

El agente conversacional también puede leer el spec: pregúntale "¿qué alcance tiene SCRUM-14?" y usa la tool `consultar_spec`.

### Dashboard de auditoría

Por cada ticket con spec declarado, agrega todo lo que hoy hay que ir a buscar por separado: el spec (contrato y evidencia requerida), el estado en Jira, la fase del proceso y su siguiente paso, las últimas ejecuciones de pipeline (con el job/step exacto si hay una en curso), y un enlace a los PRs relacionados. Lo genera `scripts/dashboard.py` (sin dependencias externas, reutiliza las mismas tools que el asistente) en dos formatos:

- **`docs/dashboard.md`** — tabla simple. GitHub ya la renderiza al abrir el archivo en el repo, sin nada que configurar.
- **`docs/dashboard.html`** — panel visual con buscador por ticket (spec, badge de Jira, barra de avance, tarjetas de pipeline, traza completa del proceso). Los datos de **todos** los tickets se traen una sola vez al generarlo (con las credenciales del workflow) y quedan incrustados como JSON en la página; el buscador filtra en el navegador entre esos datos ya traídos, sin volver a golpear Jira ni GitHub por cada búsqueda. Sigue sin ser un servidor: es un archivo estático.

`.github/workflows/dashboard.yml` mantiene ambos actualizados: corre manual, todos los días hábiles, y cada vez que cambia algo en `specs/`.

```bash
python3 scripts/dashboard.py                                          # Markdown a stdout
python3 scripts/dashboard.py --out docs/dashboard.md
python3 scripts/dashboard.py --out-html docs/dashboard.html
```

**Para ver el panel HTML renderizado** (abrirlo desde el navegador de archivos de GitHub, `github.com/.../blob/main/docs/dashboard.html`, siempre muestra el código fuente, nunca la página) hay que habilitar GitHub Pages una vez: **Settings → Pages → Source: Deploy from a branch → Branch: `main` / `docs`**. Después queda en **`https://<owner>.github.io/<repo>/`** — el workflow también copia `dashboard.html` a `docs/index.html`, así que la raíz del sitio ya es el panel, sin tener que recordar el nombre del archivo. `docs/.nojekyll` evita que GitHub Pages intente procesar el sitio con Jekyll (que por defecto espera archivos con "front matter" y puede interferir con HTML/JS servido tal cual).

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
- `consultar_spec` — el contrato del ticket: qué debe cambiar, qué no debe tocarse, evidencia requerida

**Interfaces disponibles:**

| Archivo | Interfaz | Uso |
|---|---|---|
| `scripts/slack_bot.py` | Slack (Socket Mode) | `/release SCRUM-11` o `@DeployGo ¿por qué falló?` |
| `scripts/assistant.py` | Terminal | `python assistant.py "¿en qué va SCRUM-11?"` |
| `.github/workflows/consulta-estado.yml` | GitHub Actions | Run workflow → reporte en el Summary |
| `docs/dashboard.md` | Vista persistente (Markdown) | Se abre directo en GitHub |
| `docs/dashboard.html` | Panel visual (buscador por ticket) | Requiere GitHub Pages habilitado; ver sección "Dashboard de auditoría" |

El conocimiento del proceso vive en `scripts/process-template.yml`, un archivo editable. **Cuando el proceso cambia, se edita ese archivo — no el código.**

### Crear tickets desde Slack: `/crear-ticket`

A diferencia de las tools de arriba, `crear_ticket` y `comentar_ticket` (en `scripts/tools.py`) **no** están expuestas al agente conversacional — crear datos en Jira es una acción con efectos reales, no una consulta, así que no queda a un paso de una frase mal interpretada por el modelo. En su lugar, `/crear-ticket` en Slack abre un formulario (modal) con campos fijos: tipo, título y descripción. El proyecto de Jira es fijo (`JIRA_PROJECT_KEY`), no editable desde el formulario.

Controles antes de que algo llegue a Jira:

- **Canal restringido** — el comando solo responde en `JIRA_TICKET_CHANNEL_ID`; en cualquier otro canal, se rechaza.
- **Validación del formulario** — errores inline en el propio modal (título vacío, límites de longitud) antes de aceptar el envío.
- **Confirmación explícita de dos pasos** — el modal nunca crea el ticket directo; publica un resumen con botones *Confirmar*/*Cancelar*, y solo quien inició la creación puede confirmarla o cancelarla.
- **Rate limiting** — reutiliza el mismo límite por usuario que las consultas normales del bot.
- **Trazabilidad** — al crear el ticket, se agrega un comentario automático en el propio issue ("Creado vía DeployGo Assistant por @usuario en #canal") además del log server-side.

Después de crear el ticket, el bot ofrece un botón **"Armar spec"**: genera un borrador de `specs/<TICKET>.yml` a partir del título ya capturado, con los campos `cambios_permitidos`/`cambios_prohibidos`/`contrato`/`evidencia_requerida` marcados como `TODO` para completar. El bot **no** lo commitea solo — lo entrega listo para copiar, y el developer lo pega como primer commit de su rama, igual que el resto del flujo de specs.

---

## Configuración

### Secrets del repositorio (Actions)

`JIRA_BASE_URL` · `JIRA_EMAIL` · `JIRA_API_TOKEN` · `ANTHROPIC_API_KEY` · `DASHBOARD_BOT_TOKEN`

`DASHBOARD_BOT_TOKEN` es un PAT (classic, scope `repo`) de un admin del repositorio. `dashboard.yml` commitea `docs/dashboard.md` y `docs/dashboard.html` directo a `main`, y el ruleset bloquea eso salvo que venga de un actor en su bypass list. Con el `GITHUB_TOKEN` por defecto el push queda rechazado (`GH013`) porque `github-actions[bot]` no cuenta como admin. Dos pasos únicos para habilitarlo:

1. **Settings → Rules → Rulesets** → editar el ruleset de `main` → **Bypass list** → *Add bypass* → Roles → **Repository admin**.
2. Generar el PAT y guardarlo como secret `DASHBOARD_BOT_TOKEN` (**Settings → Secrets and variables → Actions**).

### Variables para el asistente (local o Codespaces)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export JIRA_BASE_URL="https://tusitio.atlassian.net"
export JIRA_EMAIL="tu-correo"
export JIRA_API_TOKEN="tu-token"
export GITHUB_REPO="$GITHUB_REPOSITORY"
export SLACK_BOT_TOKEN="xoxb-..."   # solo para el bot
export SLACK_APP_TOKEN="xapp-..."   # solo para el bot
export JIRA_TICKET_CHANNEL_ID="C0123456"   # opcional: habilita /crear-ticket, solo en este canal
export JIRA_PROJECT_KEY="SCRUM"            # opcional: proyecto fijo para /crear-ticket
```

Si `JIRA_TICKET_CHANNEL_ID` o `JIRA_PROJECT_KEY` no están definidos, `/crear-ticket` responde que no está configurado en vez de fallar — el resto del bot funciona igual sin ellos.

### Protección del trunk

En **Settings → Rules → Rulesets**, sobre `main`: PR obligatorio, checks requeridos `CI` y `validado-en-dev`, bloqueo de force push, solo squash merge y borrado automático de ramas. El script `scripts/setup-repo.sh` lo aplica con `gh` CLI.

En **Settings → Environments**, crear `dev` y `cert`; en `cert`, activar *Required reviewers* para el approval gate y restringir *Deployment branches* a `main`.

---

## Estructura

```
.
├── .github/workflows/     jira-branch · ci-pr · cicd-dev · cicd-cert · dashboard · consulta-estado
├── src/                   microservicio Quarkus (main y test)
├── specs/                 contrato por ticket (TEMPLATE.yml + specs/<TICKET>.yml)
├── scripts/               asistente: tools · slack_bot · assistant · dashboard · process-template
├── docs/                  estrategia de ramas + dashboard.md/dashboard.html (generados)
├── pom.xml
└── Dockerfile
```

---

## Alcance

Prueba de concepto para validar el flujo completo y la utilidad del asistente. No es un entorno productivo: no hay cluster real, los gates de seguridad son simulados y las credenciales se gestionan con secrets de repositorio.
