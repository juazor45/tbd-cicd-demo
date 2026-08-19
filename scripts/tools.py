#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools.py — Herramientas del DeployGo Assistant.

Cada función es una "tool" que el agente puede invocar:
  - consultar_jira        → estado del ticket de cambio
  - consultar_pipelines   → últimas ejecuciones de los workflows
  - detalle_ejecucion     → jobs y steps de un run (dónde se quedó)
  - consultar_proceso     → el template del proceso (fases, evidencia, siguiente paso)

Sin dependencias externas salvo `anthropic` (que usa el agente, no este módulo).
"""

import logging
import os

from http_client import age, gh_headers, http_get, jira_headers

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


def consultar_pipelines(rama=None, ticket=None):
    """Últimas ejecuciones de los workflows, opcionalmente filtradas por ticket o rama.

    La correlación con el ticket se hace por dos vías:
      - head_branch: la convención de ramas incluye la key (feature/SCRUM-11-...)
      - display_title: los workflows manuales llevan el ticket en su run-name
    """
    logger.info("tool consultar_pipelines(rama=%s, ticket=%s)", rama, ticket)
    repo = os.environ.get("GITHUB_REPO", "")
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


def detalle_ejecucion(run_id):
    """Jobs y steps de una ejecución: identifica en qué step va, falló o espera aprobación."""
    logger.info("tool detalle_ejecucion(run_id=%s)", run_id)
    repo = os.environ.get("GITHUB_REPO", "")
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
        "description": "Lista la última ejecución de cada pipeline del repositorio (Jira-Branch, CI-PR, CICD-DEV, CICD-CERT) con su estado, resultado, rama, título y run_id. Pasa 'ticket' para ver solo las ejecuciones de ese release; sin filtro devuelve las últimas del repositorio. Si el filtro por ticket no devuelve nada, vuelve a consultar sin filtro antes de concluir.",
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
                "run_id": {"type": "integer", "description": "ID del run de GitHub Actions"}
            },
            "required": ["run_id"],
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
}
