#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — Genera un dashboard de auditoría en Markdown.

Por cada ticket con spec declarado (specs/<TICKET>.yml, salvo TEMPLATE.yml)
agrega en una sola vista: el spec (título, contrato, evidencia requerida),
el estado en Jira, las últimas ejecuciones de pipeline, y un enlace a los
PRs relacionados en GitHub.

No es un servidor: genera un archivo Markdown estático. GitHub ya renderiza
el Markdown al abrir el archivo en el repo, así que no hace falta levantar
nada para tener una vista persistente y compartible — basta con abrir
docs/dashboard.md.

Uso:
  python3 scripts/dashboard.py                    # imprime a stdout
  python3 scripts/dashboard.py --out docs/dashboard.md

Pensado para correr también desde .github/workflows/dashboard.yml
(manual, programado, o cuando cambia specs/**), que commitea el resultado.

Variables de entorno: las mismas que tools.py — JIRA_BASE_URL, JIRA_EMAIL,
JIRA_API_TOKEN, GITHUB_REPO, GITHUB_TOKEN (opcional en repos públicos).
Si faltan, cada sección lo indica en vez de fallar (mismo criterio que
tools.py: nunca truena, informa qué no se pudo leer).

Sin dependencias externas: solo librería estándar de Python 3.8+.
"""

import argparse
import datetime
import glob
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import consultar_jira, consultar_pipelines  # noqa: E402
from validate_spec import SPECS_DIR, parsear_spec  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

ICONOS = {
    "success": "✅",
    "failure": "❌",
    "cancelled": "⚪",
    "skipped": "⚪",
    "en curso": "🔄",
}


def listar_tickets():
    """Todos los specs/<TICKET>.yml declarados, en orden alfabético, salvo la plantilla."""
    tickets = []
    for path in sorted(glob.glob(os.path.join(SPECS_DIR, "*.yml"))):
        nombre = os.path.basename(path)
        if nombre == "TEMPLATE.yml":
            continue
        tickets.append((nombre[:-4], path))
    return tickets


def resumen_jira(ticket):
    data = consultar_jira(ticket)
    if "error" in data:
        return f"⚠️ {data['error']}"
    return f"**{data['estado']}** — {data['asignado_a']} ({data['actualizado']})"


def resumen_pipelines(ticket):
    data = consultar_pipelines(ticket=ticket)
    if "error" in data:
        return f"⚠️ {data['error']}"
    ejecuciones = data.get("ejecuciones", [])
    if not ejecuciones:
        return "— sin ejecuciones encontradas —"
    filas = []
    for e in ejecuciones:
        icono = ICONOS.get(e["resultado"], "❔")
        filas.append(f"{icono} [{e['workflow']}]({e['url']}) — {e['resultado']} ({e['ejecutado']})")
    return "<br>".join(filas)


def generar(repo):
    tickets = listar_tickets()
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["# Dashboard de auditoría", ""]
    out.append(
        f"_Generado automáticamente el {ahora} por `scripts/dashboard.py`. "
        "No editar a mano — lo regenera `.github/workflows/dashboard.yml`._"
    )

    if not tickets:
        out.append("\nTodavía no hay ningún spec declarado en `specs/`.")
        return "\n".join(out) + "\n"

    out.append("")
    out.append("| Ticket | Spec | Jira | Pipelines | PRs |")
    out.append("|---|---|---|---|---|")
    for ticket, path in tickets:
        spec = parsear_spec(path)
        titulo = spec.get("titulo", ticket)
        jira = resumen_jira(ticket)
        pipelines = resumen_pipelines(ticket)
        prs_url = f"https://github.com/{repo}/pulls?q=is%3Apr+{ticket}" if repo else "#"
        out.append(f"| **{ticket}** | [{titulo}](../specs/{ticket}.yml) | {jira} | {pipelines} | [Ver PRs]({prs_url}) |")

    out.append("")
    out.append("---")
    out.append("")
    out.append("## Detalle por ticket")

    for ticket, path in tickets:
        spec = parsear_spec(path)
        out.append("")
        out.append(f"### {ticket} — {spec.get('titulo', '')}")
        out.append("")
        out.append(f"**Contrato:** {spec.get('contrato', '—').strip()}")
        evidencia = spec.get("evidencia_requerida", [])
        if evidencia:
            out.append("")
            out.append("**Evidencia requerida:**")
            for item in evidencia:
                out.append(f"- {item}")

    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="Archivo de salida (por defecto: stdout)")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        logger.warning("GITHUB_REPO no está definido; los enlaces a PRs quedarán rotos.")

    contenido = generar(repo)

    if args.out:
        carpeta = os.path.dirname(args.out)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(contenido)
        logger.info("Dashboard escrito en %s", args.out)
    else:
        print(contenido)


if __name__ == "__main__":
    main()
