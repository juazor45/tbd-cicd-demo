#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
release-status.py — Asistente de estado del release (demo DeployGo)

Cruza tres fuentes para decirte dónde está un release y qué sigue:
  1. Jira API      → estado actual del ticket de cambio
  2. GitHub API    → últimas ejecuciones de los pipelines, y en cuál job/step van o fallaron
  3. Template YAML → mapa del proceso (fase, evidencia, siguiente paso)

Uso:
  python release-status.py SCRUM-10

Variables de entorno requeridas (las mismas del pipeline):
  JIRA_BASE_URL   https://tusitio.atlassian.net
  JIRA_EMAIL      correo de Atlassian
  JIRA_API_TOKEN  API token de Atlassian
  GITHUB_REPO     owner/repo   (ej. juanzorrilla/tbd-cicd-demo)
  GITHUB_TOKEN    (opcional en repos públicos; recomendado para evitar rate limit)

Sin dependencias externas: solo librería estándar de Python 3.8+.
El template se lee con un parser mínimo (no requiere PyYAML).
"""

import os
import sys

from http_client import age as fmt_age
from http_client import gh_headers as _gh_headers
from http_client import http_get, jira_headers

WORKFLOWS = ["Jira-Branch", "CI-PR", "CICD-DEV", "CICD-CERT"]
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "process-template.yml")


def gh_headers():
    return _gh_headers("release-status-demo")


# ----------------------------------------------------------------------
# 1) JIRA: estado del ticket
# ----------------------------------------------------------------------
def get_jira_status(ticket):
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not (base and email and token):
        print("❌ Faltan JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN en el entorno.")
        sys.exit(1)

    code, data = http_get(
        f"{base}/rest/api/3/issue/{ticket}?fields=status,summary,updated",
        headers=jira_headers(),
    )
    if code != 200:
        print(f"❌ No se pudo leer {ticket} en Jira (HTTP {code}). ¿Existe? ¿Credenciales OK?")
        sys.exit(1)
    f = data["fields"]
    return {
        "summary": f["summary"],
        "status": f["status"]["name"],
        "updated": f.get("updated", ""),
    }


# ----------------------------------------------------------------------
# 2) GITHUB: runs de los workflows y job/step actual o fallido
# ----------------------------------------------------------------------
def get_workflow_runs(repo):
    """Última ejecución de cada workflow de interés."""
    code, data = http_get(
        f"https://api.github.com/repos/{repo}/actions/runs?per_page=40",
        headers=gh_headers(),
    )
    if code != 200:
        print(f"⚠️ No se pudieron leer los runs de GitHub (HTTP {code}).")
        return {}
    latest = {}
    for run in data.get("workflow_runs", []):
        name = run.get("name")
        if name in WORKFLOWS and name not in latest:
            latest[name] = run
    return latest


def get_run_steps(repo, run_id):
    """Jobs y steps de un run: devuelve (job, step) en curso o fallido, y resumen."""
    code, data = http_get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs",
        headers=gh_headers(),
    )
    if code != 200:
        return None
    detail = {"jobs": [], "pointer": None}
    for job in data.get("jobs", []):
        jinfo = {"name": job["name"], "status": job["status"], "conclusion": job["conclusion"]}
        detail["jobs"].append(jinfo)
        for step in job.get("steps", []):
            st, cc = step["status"], step["conclusion"]
            if cc == "failure" and not detail["pointer"]:
                detail["pointer"] = ("❌ falló en", job["name"], step["name"])
            elif st == "in_progress" and not detail["pointer"]:
                detail["pointer"] = ("▶️ ejecutando", job["name"], step["name"])
        if job["status"] == "waiting" and not detail["pointer"]:
            detail["pointer"] = ("⏸️ esperando aprobación", job["name"], "environment approval")
    return detail


# ----------------------------------------------------------------------
# 3) TEMPLATE: parser mínimo del YAML del proceso
# ----------------------------------------------------------------------
def load_template():
    phases = {}
    try:
        current = {}
        for raw in open(TEMPLATE_FILE, encoding="utf-8"):
            line = raw.strip()
            if line.startswith("- jira_status:"):
                if current.get("jira_status"):
                    phases[current["jira_status"]] = current
                current = {"jira_status": line.split(":", 1)[1].strip().strip('"')}
            elif ":" in line and current is not None and not line.startswith("#"):
                key, val = line.split(":", 1)
                if key in ("phase", "description", "evidence", "next_step"):
                    current[key] = val.strip().strip('"')
        if current.get("jira_status"):
            phases[current["jira_status"]] = current
    except FileNotFoundError:
        print(f"⚠️ No se encontró {TEMPLATE_FILE}; se omite la guía del proceso.")
    return phases


# ----------------------------------------------------------------------
# Reporte
# ----------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Uso: python release-status.py <TICKET>   (ej. SCRUM-10)")
        sys.exit(1)
    ticket = sys.argv[1].upper()
    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        print("❌ Define GITHUB_REPO=owner/repo en el entorno.")
        sys.exit(1)

    print("=" * 70)
    print(f"🔎 ESTADO DEL RELEASE — ticket {ticket} | repo {repo}")
    print("=" * 70)

    # --- Jira ---
    jira = get_jira_status(ticket)
    print(f"\n📋 JIRA")
    print(f"   [{ticket}] {jira['summary']}")
    print(f"   Estado: {jira['status']}   (actualizado {fmt_age(jira['updated'])})")

    # --- GitHub ---
    print(f"\n🐙 GITHUB ACTIONS (última ejecución por pipeline)")
    runs = get_workflow_runs(repo)
    pointer_msgs = []
    for wf in WORKFLOWS:
        run = runs.get(wf)
        if not run:
            print(f"   {wf:<10} — sin ejecuciones")
            continue
        status = run["status"]
        concl = run["conclusion"] or "—"
        icon = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(concl, "▶️" if status == "in_progress" else "⏸️" if status == "waiting" else "•")
        print(f"   {wf:<10} {icon} {status}/{concl}  rama:{run['head_branch']}  {fmt_age(run['created_at'])}")
        if status in ("in_progress", "waiting", "queued") or concl == "failure":
            detail = get_run_steps(repo, run["id"])
            if detail and detail["pointer"]:
                verb, job, step = detail["pointer"]
                msg = f"{wf}: {verb} → job '{job}' / step '{step}'"
                pointer_msgs.append(msg)
                print(f"              └─ {verb}: job '{job}' → step '{step}'")

    # --- Cruce con el template ---
    phases = load_template()
    info = phases.get(jira["status"])
    print(f"\n🧭 DIAGNÓSTICO DEL PROCESO")
    if info:
        print(f"   Fase actual : {info.get('phase')}")
        print(f"   Qué significa: {info.get('description')}")
        print(f"   Evidencia esperada: {info.get('evidence')}")
        if pointer_msgs:
            print(f"   Situación en vivo:")
            for m in pointer_msgs:
                print(f"     • {m}")
        print(f"\n   👉 SIGUIENTE PASO: {info.get('next_step')}")
    else:
        print(f"   El estado '{jira['status']}' no está mapeado en el template del proceso.")
        print(f"   Revisa process-template.yml o corrige el estado del ticket.")
    print()


if __name__ == "__main__":
    main()
