# Guía de instalación paso a paso

Walkthrough completo para levantar `tbd-cicd-demo` desde cero: repo, Jira, Azure (Container Apps + bot de Teams), y el asistente. Seguí el orden -- cada sección depende de la anterior. Todos los pasos marcados con ⚠️ son problemas reales que aparecieron durante la puesta en marcha de este mismo proyecto; ya vienen resueltos en el código/scripts, pero se documentan acá para que no haya que redescubrirlos.

Tiempo estimado: 1-2 horas la primera vez (la mayor parte es esperar aprovisionamiento de Azure y, si hace falta, el trial de Microsoft 365).

---

## 0. Prerrequisitos

- Cuenta de **GitHub** con permisos de admin sobre el repo (o forkeando este).
- Cuenta de **Jira Cloud** (gratis) con un proyecto tipo Scrum creado.
- Cuenta de **Azure** con una suscripción activa (el free tier / $200 de crédito de una cuenta nueva alcanza de sobra).
- Cuenta de **Anthropic** (console.anthropic.com) con una API key.
- Opcional, solo si vas a usar el bot de **Microsoft Teams**: un tenant de Teams "para el trabajo o la escuela" (no personal/gratuito) -- ver sección 6, ahí se explica cómo conseguir uno si no tenés.
- `gh` CLI instalado y autenticado (`gh auth login`), para `scripts/setup-repo.sh`.

---

## 1. Clonar y configurar el repositorio

```bash
git clone https://github.com/<owner>/tbd-cicd-demo.git
cd tbd-cicd-demo
```

### 1.1 Protecciones de rama, environments y merge strategy

```bash
./scripts/setup-repo.sh <owner> <repo>
```

Esto configura: squash-merge únicamente + borrado automático de ramas, branch protection en `main` (PR obligatorio, checks `CI` y `validado-en-dev` requeridos, sin force-push ni borrado), protección del patrón de tags `v*`, y crea los environments `dev` y `cert`.

**Paso manual que el script no puede hacer por API**: en **Settings → Environments → cert**, activá **Required reviewers** (agregate a vos mismo) y restringí **Deployment branches** a `main`. Esto es lo que obliga la aprobación humana antes de certificar.

### 1.2 Secrets del repositorio

**Settings → Secrets and variables → Actions → Secrets**:

| Secret | De dónde sale |
|---|---|
| `JIRA_BASE_URL` | `https://tusitio.atlassian.net` |
| `JIRA_EMAIL` | tu correo de Atlassian |
| `JIRA_API_TOKEN` | Atlassian → *Manage account* → *Security* → *Create and manage API tokens* |
| `ANTHROPIC_API_KEY` | console.anthropic.com → *API Keys* |
| `DASHBOARD_BOT_TOKEN` | ver 1.3 abajo |

### 1.3 `DASHBOARD_BOT_TOKEN` (para que el dashboard se pueda auto-commitear)

`dashboard.yml` commitea `docs/dashboard.md`/`docs/dashboard.html` directo a `main`. El ruleset de `main` bloquea eso salvo que el actor esté en la bypass list:

1. **Settings → Rules → Rulesets** → el ruleset de `main` → **Bypass list** → *Add bypass* → Roles → **Repository admin**.
2. Generá un PAT classic con scope `repo` desde una cuenta admin, y guardalo como el secret `DASHBOARD_BOT_TOKEN`.

⚠️ **No pegues este ni ningún otro token/PAT en texto plano repetidas veces en un chat.** Si en algún momento lo hiciste para pedirle ayuda a un asistente de IA con un push, revocalo y generá uno nuevo apenas termines -- un PAT visto por un tercero (aunque sea un asistente) debe tratarse como comprometido.

---

## 2. Jira

1. Creá un proyecto tipo **Scrum** (o usá uno existente) y anotá su **key** (ej. `SCRUM`) -- la vas a necesitar como `JIRA_PROJECT_KEY` más adelante.
2. El proyecto necesita, como mínimo, un flujo de estados que incluya (los nombres exactos importan, son los que usan los workflows):
   `Construcción Doing → Construcción Done → Congelamiento Doing → Congelamiento Done → QA Testing Doing → QA Testing Done`
3. (Opcional pero recomendado) configurá la regla de **Jira Automation** para el disparo automático de `release.yml` -- ver README, sección 6, "Disparo automático (Jira Automation)".

---

## 3. Probar el asistente en local (opcional, sirve para validar credenciales)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export JIRA_BASE_URL="https://tusitio.atlassian.net"
export JIRA_EMAIL="tu-correo"
export JIRA_API_TOKEN="tu-token"
export GITHUB_REPO="<owner>/tbd-cicd-demo"

python3 scripts/assistant.py "¿en qué va SCRUM-11?"
```

Si responde (aunque sea "no encontré ese ticket"), las credenciales de Jira/Anthropic están bien y podés seguir.

---

## 4. Azure: Container Apps (obligatorio para que `cicd-dev.yml`/`cicd-cert.yml` desplieguen algo real)

### 4.1 Provisionar infraestructura

Corré esto en **[Azure Cloud Shell](https://shell.azure.com)** (viene autenticado, no requiere instalar nada localmente):

```bash
git clone https://github.com/<owner>/tbd-cicd-demo.git
cd tbd-cicd-demo
./scripts/setup-azure.sh <owner>/tbd-cicd-demo
```

Crea: resource group, Log Analytics, Container Apps Environment (plan Consumption), dos Container Apps (`*-dev`/`*-cert`, arrancan con imagen placeholder), y una App Registration con *federated credentials* para login OIDC sin secretos.

⚠️ **Si tu organización de GitHub es nueva (después de mediados de julio de 2026)**, el script ya resuelve esto solo: GitHub cambió el formato del "subject" OIDC a uno inmutable que incluye IDs numéricos de owner/repo, no solo los nombres. El script detecta esto automáticamente vía la API pública de GitHub y arma el subject correcto. Si por algún motivo el login falla con `AADSTS700213: No matching federated identity record found`, simplemente volvé a correr `setup-azure.sh` -- es idempotente y siempre borra-y-recrea las federated credentials.

Al final imprime valores para pegar en **Settings → Secrets and variables → Actions → Variables** (no Secrets -- son identificadores, no son sensibles):

| Variable | Ejemplo |
|---|---|
| `AZURE_CLIENT_ID` | `00000000-...` |
| `AZURE_TENANT_ID` | `00000000-...` |
| `AZURE_SUBSCRIPTION_ID` | `00000000-...` |
| `AZURE_RESOURCE_GROUP` | `rg-tbd-cicd-demo` |
| `AZURE_APP_DEV` | `ms-exchange-rate-dev` |
| `AZURE_APP_CERT` | `ms-exchange-rate-cert` |

### 4.2 Correr el primer deploy real

**Actions → CICD-DEV → Run workflow**. Al terminar, el paquete de imagen Docker en GHCR queda **privado** por defecto -- Azure Container Apps necesita poder *pull*earlo:

**GitHub → pestaña Packages del repo → `ms-exchange-rate` → Package settings → Change visibility → Public.**

Volvé a correr **CICD-DEV** si el primer intento falló por esto (imagen no encontrada / 401 al hacer pull).

Repetí para **CICD-CERT** (solo corre desde `main`, así que primero mergeá algo a `main` con un PR).

### 4.3 (Opcional) Apagar/encender para ahorrar

**Actions → Azure - Apagar / Azure - Encender**, eligiendo `dev`, `cert` o `ambas`. Container Apps ya escala a 0 sola sin tráfico, así que en uso normal el costo esperado es **$0/mes**; estos workflows son para garantizar el apagado durante un período específico.

---

## 5. Configuración adicional del asistente (Slack, opcional -- ver sección 6 para Teams)

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."
export JIRA_TICKET_CHANNEL_ID="C0123456"   # habilita /crear-ticket solo en este canal
export JIRA_PROJECT_KEY="SCRUM"
python3 scripts/slack_bot.py
```

---

## 6. Azure: Bot de Microsoft Teams (opcional -- solo si vas a usar Teams en vez de/además de Slack)

### 6.1 Conseguir un tenant de Teams válido

⚠️ **El canal de Teams de Azure Bot Service requiere un tenant "para el trabajo o la escuela" (Microsoft 365/Entra ID). No funciona con una cuenta de Teams personal/gratuita** (`teams.live.com`, "Comunidades"). Si ya tenés acceso a un tenant de tu organización, saltá a 6.2. Si no:

- **Opción A -- Microsoft 365 Developer Program**: gratis indefinidamente, pero Microsoft aplica criterios de elegibilidad caso por caso; puede rechazar la solicitud con un mensaje del estilo "You don't currently qualify...". No hay forma de forzarlo.
- **Opción B -- Microsoft 365 Business Standard, trial de 30 días**: crea un tenant `*.onmicrosoft.com` nuevo, sin depender de una organización existente. $0 el primer mes, se autorenueva a un plan pago (~$168/año) salvo que lo canceles antes de que termine el trial -- **poné un recordatorio**, esto es un compromiso financiero real y nadie más que vos puede cancelarlo.

### 6.2 Provisionar infraestructura

Requiere haber corrido `setup-azure.sh` antes (usa el mismo resource group). En Azure Cloud Shell:

```bash
./scripts/setup-azure-teams-bot.sh <owner>/tbd-cicd-demo
```

Crea: Storage Account (lo exige el runtime de Functions), una User-Assigned Managed Identity (autenticación sin client secret), una Function App (Python 3.11, Consumption), y un recurso de Azure Bot (tier F0, gratis) con el canal de Teams habilitado.

Imprime `AZURE_FUNCTIONAPP_NAME`, `MicrosoftAppId` (client id de la Managed Identity) y el messaging endpoint -- ninguno es secreto.

Cargá como **Variables** del repo:

| Variable | Ejemplo |
|---|---|
| `AZURE_FUNCTIONAPP_NAME` | `func-tbd-teamsbot-1234567890` |
| `JIRA_PROJECT_KEY` | `SCRUM` |
| `TEAMS_TICKET_CHANNEL_ID` | ver 6.5 -- todavía no lo tenés en este punto, dejalo vacío por ahora |

### 6.3 Desplegar el código del bot

**Actions → Teams Bot - Deploy → Run workflow.**

Este workflow ya incorpora dos fixes no obvios, por si estás mirando el YAML y te preguntás por qué está así:

- ⚠️ **Instala Azure Functions Core Tools vía APT, no vía `npm install -g`.** El paquete de npm da errores `401 Unable to authenticate` intermitentes en runners de GitHub Actions -- es un problema del lado del paquete npm, no de credenciales del repo. La alternativa documentada por Microsoft (repositorio APT de `packages.microsoft.com`) es estable.
- ⚠️ **Copia `tools.py`/`http_client.py`/`process-template.yml`/`specs/` dentro de `scripts/teams_bot/` antes de publicar**, porque `func azure functionapp publish` solo empaqueta el directorio donde corre. Si alguna vez agregás un módulo nuevo que el bot importa, agregalo también a este paso de sincronización o el deploy va a fallar con un `ModuleNotFoundError` en producción aunque localmente funcione.

El workflow también configura `ANTHROPIC_API_KEY`/`JIRA_*` en la Function App reutilizando los Secrets que ya cargaste en el paso 1.2 -- no hace falta cargarlos de nuevo a mano ni pasárselos a nadie.

### 6.4 Armar e instalar el manifest en Teams

```bash
cd scripts/teams_bot/teams-app-manifest
# reemplazá REEMPLAZAR_CON_MicrosoftAppId (2 apariciones) por el MicrosoftAppId de 6.2
zip deploygo-assistant.zip manifest.json color.png outline.png
```

⚠️ El manifest **no** lleva un campo `packageName` -- el schema v1.19 de Teams no lo admite (`additionalProperties: false` en la raíz); el identificador único de la app es el campo `id` (un GUID), que ya está seteado al `MicrosoftAppId`. Si alguna vez editás el manifest a mano y agregás `packageName` "porque en otros manifests existe", vas a obtener un error de validación al subirlo.

En Teams: **Apps → Administrar tus apps → Cargar una app personalizada**, seleccioná el `.zip`.

⚠️ Si aparece **"No se encuentra esta aplicación"**, es la política de tenant "Cargar aplicaciones personalizadas", desactivada por defecto en tenants nuevos: **Centro de administración de Teams** (`admin.teams.microsoft.com`) → *Aplicaciones de Teams* → *Políticas de configuración* → `Global` → habilitar *Cargar aplicaciones personalizadas*. Puede tardar unos minutos en propagarse.

### 6.5 Obtener el `TEAMS_TICKET_CHANNEL_ID` real

Con la app ya instalada y el bot respondiendo a preguntas (aunque `/crear-ticket` todavía no funcione en ningún lado):

- Si vas a usar un **canal de equipo**: los 3 puntos del canal → *Obtener vínculo al canal* → copiá el ID que empieza con `19:`.
- Si vas a usarlo en un **chat 1:1** con el bot (no hay menú de "obtener vínculo" para chats personales): escribile cualquier cosa al bot desde ese chat. Como `TEAMS_TICKET_CHANNEL_ID` todavía está vacío o no coincide, el bot va a rechazar cualquier intento de `/crear-ticket` mostrando **el ID real de esa conversación** en su propio mensaje de rechazo (`El ID de esta conversación es: ...`). Copiá ese valor.

Actualizá la Variable `TEAMS_TICKET_CHANNEL_ID` en GitHub con el valor obtenido, y **volvé a correr Teams Bot - Deploy** (paso 6.3) -- ⚠️ **cambiar la Variable en GitHub no alcanza por sí solo**: GitHub Variables no se sincronizan solas a Azure, solo lo hace el paso "Configurar variables de la Function App" de ese workflow, cada vez que corre.

---

## 7. Verificación final

- [ ] `CICD-DEV` corrió verde y `curl https://<url-dev>/q/health/live` responde `UP`.
- [ ] Un PR de prueba muestra los checks `CI` (obligatorio) y, tras correr CICD-DEV desde su rama, `validado-en-dev`.
- [ ] `CICD-CERT` corrido desde `main` pide aprobación en el environment `cert` y despliega.
- [ ] El asistente (terminal, Slack o Teams) responde correctamente a `¿en qué va <TICKET>?` para un ticket real.
- [ ] `/crear-ticket` (Slack y/o Teams) completa el flujo de dos pasos y el ticket aparece en Jira con el comentario de trazabilidad.
- [ ] `docs/dashboard.html` (si habilitaste GitHub Pages) muestra al menos un ticket con spec.

Si algo de esto falla, la causa más común en cada paso ya está documentada arriba con un ⚠️ -- revisá esa sección antes de asumir que es un problema nuevo.
