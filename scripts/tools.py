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

import base64
import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone

WORKFLOWS = ["Jira-Branch", "CI-PR", "CICD-DEV", "CICD-CERT"]
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "process-template.yml")

SSL_CTX = ssl.create_default_context()
if os.environ.get("SSL_NO_VERIFY") == "1":
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def _jira_headers():
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Accept": "application/json"}


def _gh_headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "deploygo-assistant"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _age(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        m = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        return f"hace {m} min" if m < 120 else f"hace {m // 60} h"
    except Exception:
        return "fecha desconocida"


# ----------------------------------------------------------------------
# TOOLS
# ----------------------------------------------------------------------
def consultar_jira(ticket):
    """Estado actual de un ticket de cambio en Jira."""
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    code, data = _http_get(
        f"{base}/rest/api/3/issue/{ticket}?fields=status,summary,updated,assignee",
        _jira_headers(),
    )
    if code != 200:
        return {"error": f"No se pudo leer {ticket} (HTTP {code}). Puede no existir o fallar la autenticación."}
    f = data["fields"]
    asignado = (f.get("assignee") or {}).get("displayName", "sin asignar")
    return {
        "ticket": ticket,
        "titulo": f["summary"],
        "estado": f["status"]["name"],
        "asignado_a": asignado,
        "actualizado": _age(f.get("updated", "")),
    }


def consultar_pipelines(rama=None):
    """Última ejecución de cada workflow del repositorio."""
    repo = os.environ.get("GITHUB_REPO", "")
    code, data = _http_get(
        f"https://api.github.com/repos/{repo}/actions/runs?per_page=50", _gh_headers()
    )
    if code != 200:
        return {"error": f"No se pudieron leer los runs (HTTP {code}). Revisa GITHUB_REPO y GITHUB_TOKEN."}
    vistos, out = set(), []
    for run in data.get("workflow_runs", []):
        name = run.get("name")
        if name not in WORKFLOWS or name in vistos:
            continue
        if rama and run.get("head_branch") != rama:
            continue
        vistos.add(name)
        out.append({
            "workflow": name,
            "run_id": run["id"],
            "estado": run["status"],
            "resultado": run["conclusion"] or "en curso",
            "rama": run["head_branch"],
            "ejecutado": _age(run["created_at"]),
            "url": run["html_url"],
        })
    return {"repositorio": repo, "ejecuciones": out}


def detalle_ejecucion(run_id):
    """Jobs y steps de una ejecución: identifica en qué step va, falló o espera aprobación."""
    repo = os.environ.get("GITHUB_REPO", "")
    code, data = _http_get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs", _gh_headers()
    )
    if code != 200:
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
    return {"run_id": run_id, "punto_actual": punto or "Ejecución finalizada sin pendientes", "jobs": jobs}


def consultar_proceso():
    """El template del proceso: fases, qué las evidencia y cuál es el siguiente paso."""
    try:
        return {"template": open(TEMPLATE_FILE, encoding="utf-8").read()}
    except FileNotFoundError:
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
        "description": "Lista la última ejecución de cada pipeline del repositorio (Jira-Branch, CI-PR, CICD-DEV, CICD-CERT) con su estado, resultado, rama y run_id. Úsala para saber qué se ejecutó y si algo falló o está en curso.",
        "input_schema": {
            "type": "object",
            "properties": {
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
