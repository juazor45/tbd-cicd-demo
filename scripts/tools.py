#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools.py — Herramientas del DeployGo Assistant.

Cada función es una "tool" que el agente puede invocar:
  - consultar_jira        → estado del ticket de cambio
  - consultar_pipelines   → últimas ejecuciones de los workflows
  - detalle_ejecucion     → jobs y steps de un run (dónde se quedó)
  - consultar_proceso     → el template del proceso (fases, evidencia, siguiente paso)

crear_ticket(), comentar_ticket() y listar_tipos_issue() (creacion de issues en
Jira) viven en este archivo por consistencia, pero a proposito NO estan en
TOOL_SCHEMAS/TOOL_FUNCTIONS:
no son tools que el agente conversacional pueda invocar libremente por texto libre.
Las llama directo scripts/slack_bot.py, y solo despues de pasar por su propio
control de acceso (canal autorizado), confirmacion explicita del usuario, y rate
limiting -- ver el comando /crear-ticket ahi. Mantenerlas fuera del loop agentico
es intencional: crear datos en Jira es una accion con efectos reales, no una
consulta, y no debe quedar a un paso de una frase mal interpretada por el modelo.

Sin dependencias externas salvo `anthropic` (que usa el agente, no este módulo).
"""

import logging
import os
import re

from http_client import age, gh_headers, http_get, http_post, jira_headers

logger = logging.getLogger(__name__)

WORKFLOWS = ["Jira-Branch", "CI-PR", "CICD-DEV", "CICD-CERT"]
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "process-template.yml")


# ----------------------------------------------------------------------
# TOOLS
# ----------------------------------------------------------------------
def consultar_jira(ticket):
    """Estado actual de un ticket de cambio en Jira."""
    logger.info("tool consultar_jira(ticket=%s)", ticket)
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    code, data = http_get(
        f"{base}/rest/api/3/issue/{ticket}?fields=status,summary,updated,assignee",
        jira_headers(),
    )
    if code != 200:
        logger.warning("tool consultar_jira(%s) fallo: HTTP %s", ticket, code)
        return {"error": f"No se pudo leer {ticket} (HTTP {code}). Puede no existir o fallar la autenticación."}
    f = data["fields"]
    asignado = (f.get("assignee") or {}).get("displayName", "sin asignar")
    logger.info("tool consultar_jira(%s) OK: estado=%s", ticket, f["status"]["name"])
    return {
        "ticket": ticket,
        "titulo": f["summary"],
        "estado": f["status"]["name"],
        "asignado_a": asignado,
        "actualizado": age(f.get("updated", "")),
    }


# ----------------------------------------------------------------------
# Resolucion de repositorio por ticket (soporte multi-repo)
# ----------------------------------------------------------------------
# GITHUB_REPO es una unica variable de entorno fija por instancia del bot --
# si hay varios microservicios en varios repos, no hay forma de que ese valor
# fijo apunte al repo correcto para cada ticket. En vez de eso, resolver_repo
# busca en los propios comentarios del ticket de Jira un link a github.com:
# jira-branch.yml ya deja uno al crear la rama, y release.yml deja otro al
# publicar. Si lo encuentra, ese es el repo real del ticket -- si no, quien
# llama cae de vuelta a GITHUB_REPO (comportamiento de siempre, single-repo).
_REPO_CACHE = {}
_REPO_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def _extraer_texto_adf(nodo):
    """Extrae texto plano de un nodo/documento en Atlassian Document Format
    (recursivo: parrafos, listas, etc. son 'content' anidado)."""
    partes = []
    if isinstance(nodo, dict):
        if nodo.get("type") == "text":
            partes.append(nodo.get("text", ""))
        for hijo in nodo.get("content", []) or []:
            partes.append(_extraer_texto_adf(hijo))
    elif isinstance(nodo, list):
        for hijo in nodo:
            partes.append(_extraer_texto_adf(hijo))
    return " ".join(p for p in partes if p)


def resolver_repo(ticket):
    """Busca a que repo de GitHub pertenece un ticket, leyendo sus comentarios
    de Jira. Devuelve 'owner/repo' o None si no encontro ningun link. Cachea
    en memoria por ticket (dentro del mismo proceso) para no releer los
    comentarios en cada consulta."""
    ticket = (ticket or "").upper().strip()
    if not ticket:
        return None
    if ticket in _REPO_CACHE:
        return _REPO_CACHE[ticket]

    logger.info("resolver_repo(%s): buscando link a github.com en comentarios", ticket)
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    code, data = http_get(
        f"{base}/rest/api/3/issue/{ticket}/comment?orderBy=created&maxResults=100",
        jira_headers(),
    )
    if code != 200:
        logger.warning("resolver_repo(%s): no se pudieron leer comentarios (HTTP %s)", ticket, code)
        return None

    repo = None
    for comentario in data.get("comments", []):
        texto = _extraer_texto_adf(comentario.get("body"))
        match = _REPO_URL_RE.search(texto)
        if match:
            repo = f"{match.group(1)}/{match.group(2)}"
            break

    logger.info("resolver_repo(%s): %s", ticket, repo or "sin link a github.com en los comentarios")
    _REPO_CACHE[ticket] = repo
    return repo


def consultar_pipelines(rama=None, ticket=None):
    """Últimas ejecuciones de los workflows, opcionalmente filtradas por ticket o rama.

    La correlación con el ticket se hace por dos vías:
      - head_branch: la convención de ramas incluye la key (feature/SCRUM-11-...)
      - display_title: los workflows manuales llevan el ticket en su run-name
    """
    logger.info("tool consultar_pipelines(rama=%s, ticket=%s)", rama, ticket)
    repo = (ticket and resolver_repo(ticket)) or os.environ.get("GITHUB_REPO", "")
    code, data = http_get(
        f"https://api.github.com/repos/{repo}/actions/runs?per_page=100", gh_headers()
    )
    if code != 200:
        logger.warning("tool consultar_pipelines fallo: HTTP %s", code)
        return {"error": f"No se pudieron leer los runs (HTTP {code}). Revisa GITHUB_REPO y GITHUB_TOKEN."}

    clave = (ticket or "").upper().strip()
    vistos, out = set(), []
    for run in data.get("workflow_runs", []):
        name = run.get("name")
        if name not in WORKFLOWS or name in vistos:
            continue
        if rama and run.get("head_branch") != rama:
            continue
        if clave:
            contexto = f"{run.get('head_branch', '')} {run.get('display_title', '')}".upper()
            if clave not in contexto:
                continue
        vistos.add(name)
        out.append({
            "workflow": name,
            "run_id": run["id"],
            "titulo": run.get("display_title", ""),
            "estado": run["status"],
            "resultado": run["conclusion"] or "en curso",
            "rama": run["head_branch"],
            "ejecutado": age(run["created_at"]),
            "url": run["html_url"],
        })

    logger.info("tool consultar_pipelines OK: %d ejecuciones encontradas", len(out))
    resultado = {"repositorio": repo, "ejecuciones": out}
    if clave and not out:
        resultado["nota"] = (
            f"No se encontraron ejecuciones asociadas a {clave}. "
            "Puede que aún no se haya lanzado ningún pipeline con ese ticket, "
            "o que se lanzara sin indicarlo. Consulta sin filtro para ver las últimas ejecuciones del repositorio."
        )
    return resultado


def detalle_ejecucion(run_id, repo=None):
    """Jobs y steps de una ejecución: identifica en qué step va, falló o espera aprobación.

    'repo' es opcional -- solo hace falta pasarlo cuando el run pertenece a un
    repositorio distinto al del bot (ver el campo 'repositorio' que devuelve
    consultar_pipelines). Sin 'repo', cae a GITHUB_REPO como siempre."""
    logger.info("tool detalle_ejecucion(run_id=%s, repo=%s)", run_id, repo)
    repo = repo or os.environ.get("GITHUB_REPO", "")
    code, data = http_get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs", gh_headers()
    )
    if code != 200:
        logger.warning("tool detalle_ejecucion(%s) fallo: HTTP %s", run_id, code)
        return {"error": f"No se pudo leer el run {run_id} (HTTP {code})."}
    jobs, punto = [], None
    for job in data.get("jobs", []):
        steps = []
        for s in job.get("steps", []):
            steps.append({"step": s["name"], "estado": s["status"], "resultado": s["conclusion"] or "—"})
            if s["conclusion"] == "failure" and not punto:
                punto = f"FALLÓ en el job '{job['name']}', step '{s['name']}'"
            elif s["status"] == "in_progress" and not punto:
                punto = f"EJECUTANDO el job '{job['name']}', step '{s['name']}'"
        if job["status"] == "waiting" and not punto:
            punto = f"ESPERANDO APROBACIÓN manual del environment en el job '{job['name']}'"
        jobs.append({
            "job": job["name"],
            "estado": job["status"],
            "resultado": job["conclusion"] or "en curso",
            "steps": steps,
        })
    logger.info("tool detalle_ejecucion(%s) OK: %s", run_id, punto or "sin pendientes")
    return {"run_id": run_id, "punto_actual": punto or "Ejecución finalizada sin pendientes", "jobs": jobs}


# SPECS_DIR permite sobreescribir dónde vive specs/, relativo a este archivo.
# Por defecto asume que tools.py vive directo en scripts/ (repo original) y
# specs/ es su hermano a nivel de repo: "../specs". El bot de Teams
# (scripts/teams_bot/) corre una copia de tools.py un nivel más adentro, así
# que su workflow de deploy copia specs/ junto a esa copia y define
# SPECS_DIR=specs para que la ruta relativa siga siendo correcta sin tocar
# esta lógica.
_SPECS_DIR_OVERRIDE = os.environ.get("SPECS_DIR")


def consultar_spec(ticket):
    """Lee el spec declarado del ticket: que debe cambiar, que no debe tocar, contrato y evidencia requerida."""
    logger.info("tool consultar_spec(ticket=%s)", ticket)
    sub = _SPECS_DIR_OVERRIDE or os.path.join("..", "specs")
    spec_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), sub, f"{ticket.upper()}.yml"
    )
    try:
        contenido = open(spec_path, encoding="utf-8").read()
        logger.info("tool consultar_spec(%s) OK: %d bytes leidos", ticket, len(contenido))
        return {"ticket": ticket.upper(), "spec": contenido}
    except FileNotFoundError:
        logger.warning("tool consultar_spec(%s) fallo: no existe %s", ticket, spec_path)
        return {"error": f"No hay un spec declarado para {ticket.upper()} (specs/{ticket.upper()}.yml)"}


def consultar_proceso():
    """El template del proceso: fases, qué las evidencia y cuál es el siguiente paso."""
    logger.info("tool consultar_proceso()")
    try:
        contenido = open(TEMPLATE_FILE, encoding="utf-8").read()
        logger.info("tool consultar_proceso() OK: %d bytes leidos", len(contenido))
        return {"template": contenido}
    except FileNotFoundError:
        logger.warning("tool consultar_proceso() fallo: no se encontro %s", TEMPLATE_FILE)
        return {"error": f"No se encontró {TEMPLATE_FILE}"}


# ----------------------------------------------------------------------
# Creacion de tickets (NO expuestas al agente conversacional -- ver docstring
# del modulo). Solo las llama el flujo de /crear-ticket en slack_bot.py.
# ----------------------------------------------------------------------
MAX_TITULO = 120
MAX_DESCRIPCION = 2000


def _adf(texto):
    """Envuelve texto plano en Atlassian Document Format: la API v3 de Jira
    exige ADF (no texto plano) para 'description' y para el cuerpo de un
    comentario. Un unico parrafo alcanza para lo que necesitamos aca."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": texto}]}],
    }


def listar_tipos_issue(proyecto):
    """Tipos de issue disponibles para crear en un proyecto (excluye subtareas).

    Usa GET /rest/api/3/issuetype/project -- el endpoint de "get issue types
    for project", que Atlassian confirmo que NO forma parte de la
    deprecacion del viejo /issue/createmeta (ese devuelve 404 desde jun-2024).
    Los nombres de tipo varian por proyecto y por idioma del sitio (ej. un
    proyecto puede no tener "Task", o llamarlo "Tarea") -- por eso esto se
    consulta en vez de asumir un set fijo.
    """
    logger.info("tool listar_tipos_issue(proyecto=%s)", proyecto)
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")

    code, proyecto_data = http_get(f"{base}/rest/api/3/project/{proyecto}", jira_headers())
    if code != 200:
        logger.warning("listar_tipos_issue: no se pudo resolver el proyecto %s (HTTP %s)", proyecto, code)
        return {"error": f"No se pudo resolver el proyecto {proyecto} (HTTP {code})."}
    project_id = proyecto_data.get("id")

    code, data = http_get(f"{base}/rest/api/3/issuetype/project?projectId={project_id}", jira_headers())
    if code != 200:
        logger.warning("listar_tipos_issue(%s) fallo: HTTP %s", proyecto, code)
        return {"error": f"No se pudieron leer los tipos de issue de {proyecto} (HTTP {code})."}

    tipos = [t["name"] for t in data if not t.get("subtask")]
    logger.info("listar_tipos_issue(%s) OK: %s", proyecto, tipos)
    return {"tipos": tipos}


def crear_ticket(proyecto, tipo, titulo, descripcion):
    """Crea un issue en Jira. Valida localmente antes de llamar a la API para dar
    mejores mensajes de error que los que devuelve Jira directo. No hace ninguna
    verificacion de canal/usuario/confirmacion -- eso es responsabilidad exclusiva
    de quien la llama (slack_bot.py), esta funcion asume que ya se decidio crear."""
    titulo = (titulo or "").strip()
    descripcion = (descripcion or "").strip()

    if not tipo:
        return {"error": "Falta el tipo de issue."}
    if not titulo:
        return {"error": "El titulo no puede estar vacio."}
    if len(titulo) > MAX_TITULO:
        return {"error": f"El titulo supera los {MAX_TITULO} caracteres ({len(titulo)})."}
    if len(descripcion) > MAX_DESCRIPCION:
        return {"error": f"La descripcion supera los {MAX_DESCRIPCION} caracteres ({len(descripcion)})."}

    logger.info("tool crear_ticket(proyecto=%s, tipo=%s, titulo=%r)", proyecto, tipo, titulo)
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    body = {
        "fields": {
            "project": {"key": proyecto},
            "issuetype": {"name": tipo},
            "summary": titulo,
            "description": _adf(descripcion) if descripcion else _adf("(sin descripcion)"),
        }
    }
    code, data = http_post(f"{base}/rest/api/3/issue", jira_headers(), body)

    if code != 201:
        # Jira devuelve el detalle de que campo fallo en 'errors' -- se lo pasamos
        # al usuario en vez de un generico "algo salio mal".
        detalle = data.get("errors") or data.get("errorMessages") or data
        logger.warning("tool crear_ticket fallo: HTTP %s -- %s", code, detalle)
        return {"error": f"Jira rechazo la creacion (HTTP {code}): {detalle}"}

    key = data.get("key", "?")
    url = f"{base}/browse/{key}"
    logger.info("tool crear_ticket OK: %s (%s)", key, url)
    return {"ticket": key, "url": url}


def comentar_ticket(ticket, texto):
    """Agrega un comentario a un issue existente. Se usa para dejar trazabilidad
    (quien creo el ticket, desde donde) -- no para conversar con el agente."""
    logger.info("tool comentar_ticket(ticket=%s)", ticket)
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    code, data = http_post(f"{base}/rest/api/3/issue/{ticket}/comment", jira_headers(), {"body": _adf(texto)})
    if code not in (200, 201):
        logger.warning("tool comentar_ticket(%s) fallo: HTTP %s", ticket, code)
        return {"error": f"No se pudo comentar {ticket} (HTTP {code})."}
    logger.info("tool comentar_ticket(%s) OK", ticket)
    return {"ok": True}


# ----------------------------------------------------------------------
# Definiciones para la API de Anthropic
# ----------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "name": "consultar_jira",
        "description": "Obtiene el estado actual de un ticket de cambio en Jira (estado del tablero, título, asignado, última actualización). Úsala siempre que el usuario mencione un ticket o pregunte por el avance de un release.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket": {"type": "string", "description": "Key del ticket, ej. SCRUM-10"}
            },
            "required": ["ticket"],
        },
    },
    {
        "name": "consultar_pipelines",
        "description": "Lista la última ejecución de cada pipeline (Jira-Branch, CI-PR, CICD-DEV, CICD-CERT) con su estado, resultado, rama, título, run_id y 'repositorio' (owner/repo real de donde salieron esas ejecuciones). Pasa 'ticket' para ver solo las ejecuciones de ese release Y para que resuelva automáticamente a qué repositorio pertenece (si hay varios microservicios en varios repos, cada ticket puede corresponder a uno distinto); sin ticket usa el repositorio por defecto del bot. Si el filtro por ticket no devuelve nada, vuelve a consultar sin filtro antes de concluir. Fijate en el campo 'repositorio' de la respuesta: si vas a llamar a detalle_ejecucion después, pasale ese mismo valor como 'repo'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket": {"type": "string", "description": "Opcional: filtra las ejecuciones relacionadas con ese ticket, ej. SCRUM-11"},
                "rama": {"type": "string", "description": "Opcional: filtrar por rama, ej. main"}
            },
        },
    },
    {
        "name": "detalle_ejecucion",
        "description": "Dado un run_id (obtenido de consultar_pipelines), devuelve sus jobs y steps e identifica exactamente en qué punto está: ejecutando, fallido o esperando aprobación manual. Úsala cuando un pipeline no esté en success para saber dónde se quedó.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer", "description": "ID del run de GitHub Actions"},
                "repo": {"type": "string", "description": "Opcional, formato 'owner/repo'. Pasa el mismo valor que trajo el campo 'repositorio' de consultar_pipelines si hay varios repos -- sin esto asume el repositorio por defecto del bot."}
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "consultar_spec",
        "description": "Devuelve el spec declarado de un ticket (specs/<TICKET>.yml): que debe cambiar, que NO debe tocar, el contrato de API a preservar, y la evidencia requerida para considerarlo terminado. Usala cuando pregunten por el alcance, los limites o los criterios de aceptacion de un ticket. Si no existe el archivo, informa que el ticket no tiene spec declarado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket": {"type": "string", "description": "Key del ticket, ej. SCRUM-20"}
            },
            "required": ["ticket"],
        },
    },
    {
        "name": "consultar_proceso",
        "description": "Devuelve el template documentado del proceso de release: cada fase, qué la evidencia y cuál es el siguiente paso. Úsala para explicar en qué fase está el release y qué debe hacer el usuario a continuación.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "consultar_jira": consultar_jira,
    "consultar_pipelines": consultar_pipelines,
    "detalle_ejecucion": detalle_ejecucion,
    "consultar_proceso": consultar_proceso,
    "consultar_spec": consultar_spec,
}
