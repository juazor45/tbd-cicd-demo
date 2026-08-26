# tbd-cicd-demo

Laboratorio funcional de un proceso de entrega de software completo: **Trunk-Based Development**, pipelines CI/CD con gestión de cambios automatizada contra Jira, y un **asistente de IA** que responde en lenguaje natural en qué punto está cada release.

> ¿Instalando esto desde cero? Andá directo a **[INSTALL.md](INSTALL.md)** -- walkthrough completo, en orden, con todos los problemas reales que aparecen en el camino ya resueltos.

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
| **Versionado** | SemVer calculado solo (`scripts/version.py`) desde tags + Conventional Commits; tag + GitHub Release al cerrar el ciclo |

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
| `cicd-dev.yml` | Manual, desde cualquier rama | CI completo + deploy real a **Azure Container Apps** (dev) con smoke test → **Construcción Done**. Al terminar, marca el commit con el status `validado-en-dev` |
| `cicd-cert.yml` | Manual, **solo desde `main`** | Valida origen y ticket, CI con policies estrictas, aprobación manual y deploy real a **Azure Container Apps** (cert) → **Congelamiento** y **QA Testing Doing** |
| `release.yml` | Manual, tras QA Testing Done | Valida el estado del ticket, taggea `vX.Y.Z` y publica el GitHub Release — ver [sección 6](#6-versionado-semver--tags-de-git) |
| `azure-apagar.yml` / `azure-encender.yml` | Manual | Fuerza a 0 o restaura a 1 las réplicas máximas de la Container App (dev/cert/ambas) — ver [sección 7](#7-infraestructura-en-azure-container-apps) |

**Etapas de CI**: Prepare → Get Secrets (Vault) → Build & Tests (Maven) → SonarQube → Fortify → Publish Artifact → Component Test → SCA Xray

**Etapas de CD**: Prepare → Build & Push a GHCR → Azure Login (OIDC) → Deploy a Azure Container Apps → Smoke Test

> Los análisis de seguridad/calidad (SonarQube, Fortify, SCA) siguen **simulados**. El build de Maven, la imagen Docker, el push al registry, el despliegue en Azure y el smoke test son **reales**.

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

**Multi-repositorio**: si el proyecto crece a varios microservicios en varios repos, el asistente no depende de un único `GITHUB_REPO` fijo para todo. `jira-branch.yml` deja un comentario con el link al repo (`github.com/<owner>/<repo>/tree/<rama>`) apenas se crea la rama de un ticket -- lo mismo que `release.yml` ya hacía al publicar el release. `resolver_repo(ticket)`, en `scripts/tools.py`, lee esos comentarios y detecta a qué repositorio pertenece cada ticket (con caché en memoria); `consultar_pipelines` lo usa automáticamente cuando se le pasa un `ticket`, y devuelve el repo resuelto en su campo `repositorio`, que el agente reenvía a `detalle_ejecucion` si hace falta. Si un ticket no tiene ningún comentario con link a GitHub (por ejemplo, si la rama se creó sin pasar por `jira-branch.yml`), cae de vuelta al `GITHUB_REPO` configurado por defecto -- el comportamiento de siempre, sin romper nada para instalaciones de un solo repo.

**Interfaces disponibles:**

| Archivo | Interfaz | Uso |
|---|---|---|
| `scripts/teams_bot/` | **Microsoft Teams** (Bot Framework, Azure Functions) | `/crear-ticket` o preguntarle directo: `¿en qué va SCRUM-11?` -- ver [sección 8](#8-bot-de-microsoft-teams-azure-bot-service--functions) |
| `scripts/slack_bot.py` | Slack (Socket Mode) -- legado, se mantiene funcional | `/release SCRUM-11` o `@DeployGo ¿por qué falló?` |
| `scripts/assistant.py` | Terminal | `python assistant.py "¿en qué va SCRUM-11?"` |
| `.github/workflows/consulta-estado.yml` | GitHub Actions | Run workflow → reporte en el Summary |
| `docs/dashboard.md` | Vista persistente (Markdown) | Se abre directo en GitHub |
| `docs/dashboard.html` | Panel visual (buscador por ticket) | Requiere GitHub Pages habilitado; ver sección "Dashboard de auditoría" |

El conocimiento del proceso vive en `scripts/process-template.yml`, un archivo editable. **Cuando el proceso cambia, se edita ese archivo — no el código.**

### Crear tickets: `/crear-ticket` (Teams y Slack)

A diferencia de las tools de arriba, `crear_ticket`, `comentar_ticket` y `listar_tipos_issue` (en `scripts/tools.py`) **no** están expuestas al agente conversacional — crear datos en Jira es una acción con efectos reales, no una consulta, así que no queda a un paso de una frase mal interpretada por el modelo. En su lugar, `/crear-ticket` abre un formulario con campos fijos (tipo, título y descripción) -- una Adaptive Card en Teams, un modal en Slack. El proyecto de Jira es fijo (`JIRA_PROJECT_KEY`), no editable desde el formulario, y el selector de *tipo* se llena con los tipos de issue reales del proyecto (`listar_tipos_issue`, consultados una vez y cacheados en memoria) — no una lista fija, porque varían por esquema de proyecto e idioma (un proyecto puede no tener "Task", o llamarlo "Tarea").

Controles antes de que algo llegue a Jira:

- **Canal restringido** — el comando solo responde en `JIRA_TICKET_CHANNEL_ID`; en cualquier otro canal, se rechaza.
- **Validación del formulario** — errores inline en el propio modal (título vacío, límites de longitud) antes de aceptar el envío.
- **Confirmación explícita de dos pasos** — el modal nunca crea el ticket directo; publica un resumen con botones *Confirmar*/*Cancelar*, y solo quien inició la creación puede confirmarla o cancelarla.
- **Rate limiting** — reutiliza el mismo límite por usuario que las consultas normales del bot.
- **Trazabilidad** — al crear el ticket, se agrega un comentario automático en el propio issue ("Creado vía DeployGo Assistant por @usuario en #canal") además del log server-side.

Después de crear el ticket, el bot ofrece un botón **"Armar spec"**: genera un borrador de `specs/<TICKET>.yml` a partir del título ya capturado, con los campos `cambios_permitidos`/`cambios_prohibidos`/`contrato`/`evidencia_requerida` marcados como `TODO` para completar. El bot **no** lo commitea solo — lo entrega listo para copiar, y el developer lo pega como primer commit de su rama, igual que el resto del flujo de specs.

---

## 6. Versionado (SemVer + tags de Git)

Antes, la versión era un campo de texto libre que alguien escribía a mano en cada `workflow_dispatch` (`1.0.0-SNAPSHOT` en dev, `1.0.0-RC1` en cert, sin relación entre sí ni con `pom.xml`, que se quedó fijo desde el día uno). Ahora se calcula sola, en un solo lugar, y ese mismo número viaja por todo el pipeline.

### Cómo se calcula

`scripts/version.py` mira el último tag `vX.Y.Z` alcanzable y los commits nuevos desde ahí, y decide el bump siguiendo [Conventional Commits](https://www.conventionalcommits.org/) (el mismo estilo que ya se usa en este repo: `feat(...):`, `fix(...):`, `chore(...):`):

| Se ve en los commits desde el último tag | Bump |
|---|---|
| Algún `feat!:`, `fix!:`, etc., o `BREAKING CHANGE` en el cuerpo | **MAJOR** (`X+1.0.0`) |
| Ningún breaking, pero algún `feat:` | **MINOR** (`X.Y+1.0`) |
| Ni breaking ni `feat:`, pero hay commits nuevos (`fix:`, `chore:`, etc.) | **PATCH** (`X.Y.Z+1`) |
| Sin commits nuevos desde el tag | Se repite la misma versión |

Si todavía no existe ningún tag en el repo, arranca desde `0.0.0`.

```bash
python3 scripts/version.py                  # X.Y.Z          -> versión de release
python3 scripts/version.py --stage dev      # X.Y.Z-dev.<sha> -> build de CICD-DEV
python3 scripts/version.py --stage rc       # X.Y.Z-rc.<sha>  -> build de CICD-CERT
python3 scripts/version.py --explain        # + el razonamiento (tag base, commits, bump) por stderr
```

### Dónde se usa esa versión

| Etapa | Quién la calcula | Qué la usa |
|---|---|---|
| `CICD-DEV` | Paso "Calcular versión" (`--stage dev`) | `mvn -Drevision=...`, el artefacto subido, el tag de la imagen Docker |
| `CICD-CERT` | Paso "Calcular versión de release candidate" (`--stage rc`) | Igual que dev, con sufijo `-rc.<sha>` |
| `Release` | Paso "Calcular versión de release" (`--stage release`, sin sufijo) | El tag anotado `vX.Y.Z` y el GitHub Release |

`pom.xml` ya no tiene la versión fija — usa el patrón *CI Friendly Versions* de Maven (`<version>${revision}</version>`, con `0.0.0-SNAPSHOT` como default para builds locales) y cada workflow se la inyecta con `mvn -Drevision=$VERSION`. Ningún commit de "bump version": el número nunca vive escrito en el código.

Los dos workflows de CI/CD conservan un input opcional `version_override` por si hace falta forzar un valor puntual (por ejemplo, para reintentar un build sin que el bump automático avance) — vacío por defecto, que es lo que dispara el cálculo automático.

### `Release`: el cierre del ciclo

`.github/workflows/release.yml` es deliberadamente el último paso, no parte de `CICD-CERT`: valida que el ticket ya esté en **QA Testing Done** en Jira (si no, aborta — igual que `CICD-CERT` valida el estado antes de certificar), calcula la versión final, crea un **tag anotado `vX.Y.Z`** sobre `main`, publica un **GitHub Release** con notas autogeneradas (compara contra el tag anterior y lista los PRs mergeados), y deja un comentario en el ticket de Jira con el link al release.

```
Construcción Doing → Construcción Done → Congelamiento Doing → Congelamiento Done
        (rama)         (CICD-DEV)          (CICD-CERT)           (CICD-CERT)
   → QA Testing Doing → QA Testing Done → [workflow Release] → tag vX.Y.Z + GitHub Release
      (CICD-CERT)        (QA, ver abajo)
```

Solo se taggea ese punto final — ni dev ni cert ensucian el historial de tags, solo generan un identificador de build trazable (`-dev.<sha>` / `-rc.<sha>`) para saber exactamente qué commit se desplegó, sin crear un tag por cada corrida. No se metió este paso dentro de `CICD-CERT` a propósito: un tag debe significar "QA lo certificó", no "el pipeline de cert pasó" — y `CICD-CERT` se re-corre seguido (flaky tests, ajustes menores) antes de que QA firme, así que taguear ahí ensuciaría el historial con releases que nunca se llegaron a entregar.

`release.yml` acepta **dos formas de dispararse**, y en ambas vuelve a consultar el estado real del ticket en Jira antes de taggear — nunca confía ciegamente en quién lo llamó:

| Disparo | Cómo |
|---|---|
| Manual | **Actions → Release → Run workflow**, con el ticket a mano |
| Automático | Una regla de **Jira Automation** dispara un evento `repository_dispatch` en cuanto el ticket pasa a "QA Testing Done" — ver abajo |

### Disparo automático (Jira Automation)

Para que el release se dispare solo, sin que nadie tenga que entrar a Actions: una regla de Jira Automation llama a la API de GitHub (`POST /repos/.../dispatches`) apenas el ticket transiciona a "QA Testing Done". GitHub recibe ese evento como `repository_dispatch` (`type: jira-qa-done`) y arranca `release.yml` igual que si alguien lo hubiera corrido a mano.

**1. Generar el token que usará Jira para llamar a GitHub**

Un Personal Access Token con permiso de escritura sobre el repo (idealmente uno *fine-grained*, acotado solo a `tbd-cicd-demo`, con permiso `Contents: Read and write` — nada más). Este token va a vivir dentro de Jira, no en los secrets del repo, así que conviene que sea uno dedicado a esto y no el mismo que uses para otra cosa.

**2. Crear la regla en Jira** (`Configuración del proyecto → Automatización → Crear regla`, o Automatización global)

- **Trigger**: *Issue transitioned* → *To status*: `QA Testing Done`.
- **Condición** (opcional pero recomendada): `Project = SCRUM` (o el proyecto que corresponda), para que la regla no dispare releases de tickets de otros proyectos.
- **Acción**: *Send web request*
  - URL: `https://api.github.com/repos/juazor45/tbd-cicd-demo/dispatches`
  - Método: `POST`
  - Headers: `Authorization: Bearer <el token del paso 1>` y `Accept: application/vnd.github+json`
  - Body (JSON personalizado):
    ```json
    {
      "event_type": "jira-qa-done",
      "client_payload": { "issue_key": "{{issue.key}}" }
    }
    ```

Si tu plan de Jira soporta *secrets* dentro de Automatización, guarda el token ahí en vez de pegarlo directo en el header — así no queda visible para cualquiera que pueda editar la regla.

**3. Probar la regla** sin esperar un ciclo completo: movés cualquier ticket a "QA Testing Done" a mano y revisás en **Actions** de GitHub que apareció una corrida de `Release` con ese ticket.

### Configuración adicional

`scripts/setup-repo.sh` ahora también protege el patrón de tags `v*` (`Settings → Tags → Tag protection rules`) para que un release publicado no se pueda borrar ni sobreescribir por error. El workflow `release.yml` empuja el tag con el `GITHUB_TOKEN` por defecto — a diferencia de `dashboard.yml`, esto no choca con el ruleset de `main` porque un tag no es una rama, así que no hace falta el PAT `DASHBOARD_BOT_TOKEN` para esto.

---

## 7. Infraestructura en Azure (Container Apps)

El microservicio se despliega en **Azure Container Apps (plan Consumption)**, no en un clúster de Kubernetes simulado. Se eligió sobre las otras opciones de Azure por una razón concreta de costo:

| Opción | Por qué se descartó / eligió |
|---|---|
| Azure App Service (B1) | Un app **detenido** en un tier de pago sigue facturando igual — hay que borrarlo o bajarlo a Free para no pagar. No sirve para "apagar y prender" |
| Azure Container Instances (ACI) | Cobra por segundo desde el primer segundo, **sin cupo gratis mensual**. Si te olvidás de apagarlo, sigue sumando costo real |
| **Azure Container Apps (Consumption)** ✅ | Tiene cupo gratis mensual (180.000 vCPU-seg + 360.000 GiB-seg + 2M requests) y **escala a 0 réplicas sola** cuando no hay tráfico — ahí no cobra nada, sin que nadie tenga que acordarse de apagarla |

Para un laboratorio de tráfico bajo, el costo esperado es **$0/mes**, cubierto por el cupo gratis.

### Arquitectura

Dos Container Apps (`ms-exchange-rate-dev` y `ms-exchange-rate-cert`) dentro de un mismo `Container Apps Environment`, cada una alimentada por su propio pipeline (`cicd-dev.yml` / `cicd-cert.yml`). La imagen se construye y se publica en **GHCR** (GitHub Container Registry, gratis) en cada deploy, y GitHub Actions se autentica contra Azure con **OIDC (federated credentials)** — sin ningún secret de Azure guardado en el repo.

```
GitHub Actions ──(OIDC, sin secretos)──▶ Azure (az containerapp update)
      │
      └─(docker push)──▶ ghcr.io/juazor45/tbd-cicd-demo/ms-exchange-rate
                                │
                                ▼
                    Azure Container Apps pull la imagen
```

### Provisionar la infraestructura (una sola vez, o para otra laptop/suscripción)

`scripts/setup-azure.sh` crea todo: resource group, Log Analytics, el Container Apps Environment, las dos Container Apps (arrancan con una imagen placeholder hasta el primer deploy real), la App Registration de Azure AD y sus *federated credentials* para que GitHub Actions pueda loguearse sin secretos.

Corre en **[Azure Cloud Shell](https://shell.azure.com)** (ya autenticado, no requiere instalar nada):

```bash
git clone https://github.com/juazor45/tbd-cicd-demo.git
cd tbd-cicd-demo
./scripts/setup-azure.sh juazor45/tbd-cicd-demo
```

Al final imprime unos valores para cargar como **Variables** del repositorio (**Settings → Secrets and variables → Actions → Variables**, no Secrets — no son sensibles, son identificadores):

| Variable | Ejemplo | Para qué |
|---|---|---|
| `AZURE_CLIENT_ID` | `00000000-...` | Identidad de la App Registration usada para el login OIDC |
| `AZURE_TENANT_ID` | `00000000-...` | Tenant de Azure AD |
| `AZURE_SUBSCRIPTION_ID` | `00000000-...` | Suscripción donde vive todo |
| `AZURE_RESOURCE_GROUP` | `rg-tbd-cicd-demo` | Resource group creado por el script |
| `AZURE_APP_DEV` | `ms-exchange-rate-dev` | Container App del entorno dev |
| `AZURE_APP_CERT` | `ms-exchange-rate-cert` | Container App del entorno cert |

**Paso manual único después del primer deploy real**: GHCR crea el paquete de la imagen como privado por defecto. Como Azure Container Apps necesita poder *pull*earla y no queremos guardar un token de larga duración solo para eso, hay que marcar el paquete como público una vez: en GitHub, pestaña **Packages** del repo → `ms-exchange-rate` → **Package settings** → **Change visibility** → **Public**. El código del microservicio no tiene nada sensible, así que esto no expone información.

### Apagar y encender para ahorrar

Aunque Container Apps ya escala a 0 sola cuando no hay tráfico, para tener control explícito (por ejemplo, garantizar $0 durante toda una noche o entre clases) están los workflows:

- **Azure - Apagar** (`azure-apagar.yml`): fuerza `max-replicas 0` — la Container App queda inalcanzable y su costo pasa a $0 garantizado.
- **Azure - Encender** (`azure-encender.yml`): vuelve a `max-replicas 1` (sigue escalando a 0 sola cuando no hay tráfico; esto solo restaura la posibilidad de que arranque).

Ambos corren desde **Actions → (el workflow) → Run workflow**, eligiendo `dev`, `cert` o `ambas`.

---

## 8. Bot de Microsoft Teams (Azure Bot Service + Functions)

El asistente conversacional y `/crear-ticket` corren también en Microsoft Teams, con la misma lógica de negocio que el bot de Slack (mismas 5 tools, mismos controles de seguridad de `/crear-ticket`, mismo rate limiting) -- lo único que cambia es la capa de transporte: Socket Mode (conexión saliente) se reemplaza por un webhook HTTP, y los modales de Block Kit se reemplazan por Adaptive Cards.

### Arquitectura

```
Teams ──▶ Azure Bot Service (F0, gratis) ──▶ Azure Function App (Consumption)
                                                     │
                                                     ├─ agent.py   (mismo loop que Slack, tools.py sin tocar)
                                                     ├─ bot.py     (Adaptive Cards en vez de Block Kit)
                                                     └─ tools.py / http_client.py / specs/ (copias sincronizadas en el deploy)
```

Autenticación sin secretos: el bot usa una **User-Assigned Managed Identity** (no un client secret) para autenticarse contra Bot Framework -- mismo principio que el OIDC de los pipelines de CI/CD.

**Costo esperado**: $0/mes para uso de laboratorio. Azure Bot Service tier F0 es gratis, y Azure Functions Consumption tiene 1 millón de ejecuciones gratis por mes (muy por encima de lo que un bot de equipo genera).

**Estado en memoria**: igual que el bot de Slack, el historial de conversación, el rate limiting y los borradores de `/crear-ticket` viven solo en memoria del proceso -- no hay base de datos. En Azure Functions Consumption esto puede notarse un poco más que en Slack, porque la instancia puede reciclarse cuando no hay actividad (lo cual también es lo que mantiene el costo en $0). Si en algún momento esto molesta, migrar ese estado a Azure Table Storage es un cambio acotado.

### Provisionar (una sola vez, en Azure Cloud Shell)

Requiere haber corrido antes `scripts/setup-azure.sh` (usa el mismo resource group):

```bash
./scripts/setup-azure-teams-bot.sh juazor45/tbd-cicd-demo
```

Crea: el Storage Account que exige el runtime de Functions, la Managed Identity, la Function App (Python 3.11, Consumption), el recurso de Azure Bot (tier F0) y habilita el canal de Teams. Al final imprime `AZURE_FUNCTIONAPP_NAME`, el `MicrosoftAppId` (client id de la Managed Identity) y el messaging endpoint -- ninguno es secreto.

Cargá como **Variables** del repo (no Secrets):

| Variable | Ejemplo |
|---|---|
| `AZURE_FUNCTIONAPP_NAME` | `func-tbd-teamsbot-1234567890` |
| `JIRA_PROJECT_KEY` | `SCRUM` |
| `TEAMS_TICKET_CHANNEL_ID` | `19:xxxxxxxx@thread.tacv2` (canal) o `a:1J_oK...` (chat 1:1 con el bot) |

Para un **canal** de equipo, el ID sale desde Teams: los 3 puntos del canal → *Obtener vínculo al canal*. Para un **chat 1:1** con el bot (caso común si todavía no tenés un canal donde habilitar "Cargar app personalizada") no existe ese menú -- la forma más simple es dejar la variable como esté, escribirle algo al bot desde ese chat, y copiar el ID real que el propio bot devuelve en su mensaje de rechazo ("El ID de esta conversación es: `...`"). Pegás ese valor como `TEAMS_TICKET_CHANNEL_ID` y volvés a correr **Teams Bot - Deploy** -- cambiar la Variable en GitHub no alcanza por sí solo, solo ese workflow la empuja a la Function App (`az functionapp config appsettings set`).

### Desplegar el código del bot

**Actions → Teams Bot - Deploy → Run workflow.** El workflow sincroniza `tools.py`/`http_client.py`/`process-template.yml`/`specs/` dentro de `scripts/teams_bot/` (para que el paquete de Functions quede autocontenido), configura `ANTHROPIC_API_KEY`/`JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` en la Function App reutilizando los mismos Secrets que ya usa `spec-review.yml`/`cicd-cert.yml` (no hace falta cargarlos de nuevo), y publica con `func azure functionapp publish`.

### Instalar la app en Teams

> ⚠️ **Requiere un tenant de "Teams para el trabajo o la escuela" (Microsoft 365/Entra ID), no Teams personal/gratuito.** El canal de Teams de Azure Bot Service no funciona contra `teams.live.com` (cuentas personales). Si no tenés ya un tenant de este tipo, la vía más simple sin depender de aprobación de una organización existente es la prueba gratuita de 30 días de **Microsoft 365 Business Standard** (crea un tenant `*.onmicrosoft.com` nuevo, $0 el primer mes, se autorenueva salvo que la canceles antes de que termine la prueba -- poné un recordatorio). El **Microsoft 365 Developer Program** es gratis de forma indefinida pero tiene criterios de elegibilidad que Microsoft aplica caso por caso y pueden rechazar la solicitud.

`scripts/teams_bot/teams-app-manifest/` tiene el manifest y los íconos. Antes de empaquetar, reemplazá `REEMPLAZAR_CON_MicrosoftAppId` en `manifest.json` (dos apariciones) por el `MicrosoftAppId` que imprimió `setup-azure-teams-bot.sh`. Después:

```bash
cd scripts/teams_bot/teams-app-manifest
zip deploygo-assistant.zip manifest.json color.png outline.png
```

En Teams: **Apps → Administrar tus apps → Cargar una app personalizada** (o *Cargar para mi equipo/organización* si tenés permisos de admin), y seleccioná el `.zip`. Agregala al canal o chat que configuraste como `TEAMS_TICKET_CHANNEL_ID` para que `/crear-ticket` funcione ahí.

Si aparece **"No se encuentra esta aplicación"** al subir el `.zip`, es porque la política de tenant "Cargar aplicaciones personalizadas" está desactivada por defecto: **Centro de administración de Teams** (`admin.teams.microsoft.com`) → *Aplicaciones de Teams* → *Políticas de configuración* → `Global` → habilitar *Cargar aplicaciones personalizadas*.

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
├── .github/workflows/     jira-branch · ci-pr · cicd-dev · cicd-cert · release · dashboard · consulta-estado
│                          azure-apagar · azure-encender · teams-bot-deploy
├── src/                   microservicio Quarkus (main y test)
├── specs/                 contrato por ticket (TEMPLATE.yml + specs/<TICKET>.yml)
├── scripts/
│   ├── tools.py / http_client.py / process-template.yml / version.py / dashboard.py
│   ├── assistant.py       interfaz de terminal
│   ├── slack_bot.py       bot de Slack (legado, funcional)
│   ├── teams_bot/         bot de Microsoft Teams (Azure Functions): bot · agent · cards · config · function_app
│   ├── setup-repo.sh      protecciones de rama/tags, environments
│   ├── setup-azure.sh     provisiona Azure Container Apps + OIDC
│   └── setup-azure-teams-bot.sh   provisiona Function App + Azure Bot + Managed Identity
├── docs/                  estrategia de ramas + dashboard.md/dashboard.html (generados)
├── INSTALL.md             guía paso a paso, desde cero, hasta un ambiente funcionando
├── pom.xml
└── Dockerfile
```

---

## Alcance

Prueba de concepto para validar el flujo completo y la utilidad del asistente. No es un entorno productivo: no hay cluster real, los gates de seguridad son simulados y las credenciales se gestionan con secrets de repositorio.
