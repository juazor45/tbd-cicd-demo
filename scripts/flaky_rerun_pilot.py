#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flaky_rerun_pilot.py — Flaky Rerun Pilot (Fase 5 del SDLC agentico: piloto de
autonomia L3).

UNICA accion que un agente de este repo puede EJECUTAR (todos los demas
agentes solo leen o informan): re-disparar los jobs fallidos de una corrida
de CI-PR o CICD-DEV, y solo cuando hay evidencia de que el fallo es flaky (el
mismo job ya paso antes en la misma rama). Nunca CICD-CERT, nunca un job de
deploy. Ver agents/flaky-rerun-pilot.yml (manifest) y
policies/agent-governance.yml (el guardrail completo, con el detalle de cada
regla).

Pensado para correr desde .github/workflows/agent-flaky-rerun.yml, disparado
por workflow_run cuando CI-PR o CICD-DEV terminan en failure -- no modifica
esos workflows.

Uso:
    python3 scripts/flaky_rerun_pilot.py <run_id> <workflow_name>
"""

import logging
import os
import sys

import agent_log
import policy
from http_client import gh_headers, http_get, http_post

AGENTE_ID = "flaky-rerun-pilot"
ACCION = "rerun_job"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _repo():
    return os.environ.get("GITHUB_REPO", "")


def _config_governance():
    """Lee max_reintentos_por_run directo de policies/agent-governance.yml --
    una sola fuente de verdad, igual criterio que agent_registry.py reusando
    policy.cargar_yaml_simple()."""
    path = os.path.join(policy.POLICIES_DIR, "agent-governance.yml")
    cfg = policy.cargar_yaml_simple(path)
    return int(cfg.get("max_reintentos_por_run", 1))


def _jobs_del_run(run_id):
    code, data = http_get(f"https://api.github.com/repos/{_repo()}/actions/runs/{run_id}/jobs", gh_headers())
    if code != 200:
        logger.warning("flaky_rerun_pilot: no se pudieron leer los jobs del run %s (HTTP %s)", run_id, code)
        return []
    return data.get("jobs", [])


def _run_data(run_id):
    return http_get(f"https://api.github.com/repos/{_repo()}/actions/runs/{run_id}", gh_headers())


def _reintentos_previos(run_id):
    """run_attempt de la API arranca en 1 -> run_attempt - 1 son los
    reintentos ya hechos (por este agente o por una persona a mano; el
    limite es sobre el run, no sobre quien lo reintento). None si no se pudo
    leer el run -- el llamador lo trata como 'no dentro del limite' (fail-closed)."""
    code, data = _run_data(run_id)
    if code != 200:
        return None
    return data.get("run_attempt", 1) - 1


def _paso_antes_en_la_misma_rama(job_nombre, rama, run_id_actual):
    """Entre las corridas recientes del mismo repo en 'rama', busca si
    'job_nombre' tuvo alguna vez conclusion 'success' -- evidencia de que el
    job funciona y este fallo puntual es flaky, no un bug real."""
    if not rama:
        return False
    code, data = http_get(
        f"https://api.github.com/repos/{_repo()}/actions/runs?branch={rama}&per_page=20",
        gh_headers(),
    )
    if code != 200:
        return False
    for run in data.get("workflow_runs", []):
        if run.get("id") == run_id_actual:
            continue
        for job in _jobs_del_run(run["id"]):
            if job.get("name") == job_nombre and job.get("conclusion") == "success":
                return True
    return False


def _pr_asociado(run_data):
    prs = run_data.get("pull_requests") or []
    return prs[0]["number"] if prs else None


def _comentar_pr(pr_number, cuerpo):
    if not pr_number:
        return False
    code, _ = http_post(
        f"https://api.github.com/repos/{_repo()}/issues/{pr_number}/comments",
        gh_headers(),
        {"body": cuerpo},
    )
    return code in (200, 201)


def _escribir_resumen(texto):
    resumen_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not resumen_path:
        return
    try:
        with open(resumen_path, "a", encoding="utf-8") as f:
            f.write(texto + "\n")
    except OSError as e:
        logger.warning("flaky_rerun_pilot: no se pudo escribir el resumen del job: %s", e)


def _notificar(pr_number, texto):
    """notificar_siempre (policies/agent-governance.yml): si hay un PR
    asociado comenta ahi (mismo mecanismo que spec-review.yml); si no, al
    menos queda en el resumen del job -- nunca se omite la notificacion."""
    if not _comentar_pr(pr_number, texto):
        _escribir_resumen(texto)


def _evaluar_impl(run_id, workflow_name):
    code, datos_run = _run_data(run_id)
    if code != 200:
        logger.error("flaky_rerun_pilot: no se pudo leer el run %s (HTTP %s)", run_id, code)
        return {"accion": "sin_evaluar", "motivo": f"no se pudo leer el run (HTTP {code})", "run_id": run_id}

    rama = datos_run.get("head_branch")
    pr_number = _pr_asociado(datos_run)
    max_reintentos = _config_governance()
    intentos_previos = _reintentos_previos(run_id)

    jobs_fallidos = [j["name"] for j in _jobs_del_run(run_id) if j.get("conclusion") == "failure"]
    todos_son_ci = bool(jobs_fallidos) and all(nombre.startswith("CI") for nombre in jobs_fallidos)
    job_paso_antes = (
        any(_paso_antes_en_la_misma_rama(j, rama, run_id) for j in jobs_fallidos)
        if jobs_fallidos else False
    )

    contexto = {
        "agente_id": AGENTE_ID,
        "accion": ACCION,
        "workflow": workflow_name,
        "intentos_previos": intentos_previos,
        "max_reintentos_por_run": max_reintentos,
        "reintentos_dentro_del_limite": intentos_previos is not None and intentos_previos < max_reintentos,
        "todos_los_fallidos_son_ci": todos_son_ci,
        "job_paso_antes": job_paso_antes,
        "jobs_fallidos_nombres": jobs_fallidos,
    }

    decision = policy.evaluar("agent-governance", contexto)

    if not decision.permitido:
        # Mismo criterio que _formatear_mensaje en policy.py: si el mensaje
        # referencia una clave que no esta en contexto, cae al mensaje crudo
        # en vez de romper.
        try:
            motivos = "; ".join(v["mensaje"].format(**contexto) for v in decision.violaciones)
        except (KeyError, IndexError):
            motivos = "; ".join(v["mensaje"] for v in decision.violaciones)

        resultado = {"accion": "no_ejecutada", "motivo": motivos, "run_id": run_id, "jobs_fallidos": jobs_fallidos}
        _notificar(
            pr_number,
            f"### 🧭 Flaky Rerun Pilot -- {workflow_name} (run {run_id})\n\n"
            f"No se re-disparo nada automaticamente.\n\n"
            f"**Motivo:** {motivos}\n\n"
            f"> Requiere revision manual.",
        )
        return resultado

    code, _ = http_post(
        f"https://api.github.com/repos/{_repo()}/actions/runs/{run_id}/rerun-failed-jobs",
        gh_headers(),
        {},
    )
    exito = code in (200, 201)
    resultado = {
        "accion": "rerun_disparado" if exito else "rerun_fallo",
        "run_id": run_id,
        "jobs_fallidos": jobs_fallidos,
        "http_status": code,
    }
    if exito:
        cuerpo = (
            f"Se re-disparo automaticamente: {', '.join(jobs_fallidos)} "
            f"(ya habian pasado antes en esta misma rama).\n\n"
            f"> Advisory -- si vuelve a fallar, no se reintenta de nuevo "
            f"(limite: {max_reintentos} por run). Requiere revision manual."
        )
    else:
        cuerpo = f"Se intento re-disparar pero fallo (HTTP {code}). Requiere revision manual."
    _notificar(pr_number, f"### 🧭 Flaky Rerun Pilot -- {workflow_name} (run {run_id})\n\n{cuerpo}")
    return resultado


# Unica funcion instrumentada: una linea limpia por evaluacion en
# agent-log.jsonl bajo agente_id="flaky-rerun-pilot" (mismo criterio que
# evaluar_readiness en deploy_readiness.py -- las lecturas internas van bare).
rerun_job = agent_log.instrumentar("rerun_job", _evaluar_impl)


def main():
    if len(sys.argv) < 3:
        print("Uso: flaky_rerun_pilot.py <run_id> <workflow_name>")
        sys.exit(1)
    run_id, workflow_name = sys.argv[1], sys.argv[2]
    logger.info("flaky_rerun_pilot: evaluando run %s (%s)", run_id, workflow_name)
    resultado = rerun_job(run_id, workflow_name)
    print(f"ℹ️ Flaky Rerun Pilot: {resultado}")


if __name__ == "__main__":
    main()
