#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — Genera el dashboard de auditoría (Markdown y HTML).

Por cada ticket con spec declarado (specs/<TICKET>.yml, salvo TEMPLATE.yml)
agrega en una sola vista: el spec (título, contrato, evidencia requerida),
el estado en Jira, la fase del proceso y su siguiente paso, las últimas
ejecuciones de pipeline (con el job/step exacto si está en curso), y un
enlace a los PRs relacionados en GitHub.

Genera dos salidas, ambas estáticas (nada de esto levanta un servidor):

  --out docs/dashboard.md    tabla en Markdown, la renderiza GitHub solo
  --out-html docs/dashboard.html   panel visual con buscador por ticket

El HTML es un solo archivo autocontenido: todos los datos de todos los
tickets se traen UNA VEZ en este script (con las credenciales del
workflow) y quedan incrustados como JSON en la página. El buscador filtra
en el navegador entre esos datos ya traídos — no vuelve a golpear Jira ni
GitHub por cada búsqueda, así que no hace falta un backend corriendo ni
se exponen credenciales en el cliente.

Uso:
  python3 scripts/dashboard.py                          # Markdown a stdout
  python3 scripts/dashboard.py --out docs/dashboard.md
  python3 scripts/dashboard.py --out-html docs/dashboard.html

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
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import TEMPLATE_FILE, consultar_jira, consultar_pipelines, detalle_ejecucion  # noqa: E402
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


# ----------------------------------------------------------------------
# Datos: tickets, spec, jira, pipelines, fases del proceso
# ----------------------------------------------------------------------
def listar_tickets():
    """Todos los specs/<TICKET>.yml declarados, en orden alfabético, salvo la plantilla."""
    tickets = []
    for path in sorted(glob.glob(os.path.join(SPECS_DIR, "*.yml"))):
        nombre = os.path.basename(path)
        if nombre == "TEMPLATE.yml":
            continue
        tickets.append((nombre[:-4], path))
    return tickets


def parsear_proceso(path):
    """Parser minimo de scripts/process-template.yml: extrae la lista de fases
    (jira_status, phase, description, evidence, next_step). Cada fase empieza
    en una linea '- jira_status: "..."'; las lineas 'campo: "valor"' que siguen
    pertenecen a esa misma fase. No es un parser YAML completo -- solo entiende
    la estructura fija de este archivo (mismo criterio que validate_spec.py)."""
    fases = []
    actual = None
    campo_re = re.compile(r'^\s*-?\s*([a-z_]+):\s*"(.*)"\s*$')
    with open(path, encoding="utf-8") as f:
        for raw in f:
            linea = raw.rstrip("\n")
            if not linea.strip() or linea.strip().startswith("#"):
                continue
            m = campo_re.match(linea)
            if not m:
                continue
            clave, valor = m.group(1), m.group(2)
            if clave == "name":
                continue  # nombre del proceso completo, no de una fase
            if clave == "jira_status" and linea.lstrip().startswith("-"):
                actual = {}
                fases.append(actual)
            if actual is not None:
                actual[clave] = valor
    return fases


def construir_ticket_data(ticket, spec_path, fases, repo):
    """Junta spec + Jira + fase del proceso + pipelines (con detalle de job/step
    si hay uno en curso) en un solo dict, listo para incrustar como JSON."""
    spec = parsear_spec(spec_path)
    jira = consultar_jira(ticket)
    jira_ok = isinstance(jira, dict) and "error" not in jira

    fase_actual, indice = None, None
    if jira_ok:
        for i, f in enumerate(fases):
            if f.get("jira_status") == jira.get("estado"):
                fase_actual, indice = f, i
                break

    pipelines_raw = consultar_pipelines(ticket=ticket)
    ejecuciones = pipelines_raw.get("ejecuciones", []) if "error" not in pipelines_raw else []
    pipelines = []
    for e in ejecuciones:
        detalle = detalle_ejecucion(e["run_id"])
        if "error" in detalle:
            detalle = None
        pipelines.append({
            "workflow": e["workflow"],
            "run_id": e["run_id"],
            "resultado": e["resultado"],
            "rama": e["rama"],
            "ejecutado": e["ejecutado"],
            "url": e["url"],
            "detalle": detalle,
        })

    return {
        "ticket": ticket,
        "titulo": spec.get("titulo", ticket),
        "jira": jira if jira_ok else None,
        "jira_error": None if jira_ok else jira.get("error", "No se pudo leer Jira."),
        "fase": fase_actual,
        "fase_indice": indice,
        "fases_total": len(fases),
        "pipelines": pipelines,
        "prs_url": f"https://github.com/{repo}/pulls?q=is%3Apr+{ticket}" if repo else "#",
        "contrato": spec.get("contrato", "").strip(),
        "evidencia_requerida": spec.get("evidencia_requerida", []),
    }


# ----------------------------------------------------------------------
# Salida Markdown
# ----------------------------------------------------------------------
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


def generar_markdown(repo):
    tickets = listar_tickets()
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["# Dashboard de auditoría", ""]
    out.append(
        f"_Generado automáticamente el {ahora} por `scripts/dashboard.py`. "
        "No editar a mano — lo regenera `.github/workflows/dashboard.yml`. "
        "Panel visual: [`docs/dashboard.html`](dashboard.html)._"
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


# ----------------------------------------------------------------------
# Salida HTML (panel de gobernanza)
# ----------------------------------------------------------------------
def generar_html(repo):
    fases = parsear_proceso(TEMPLATE_FILE)
    tickets = listar_tickets()
    datos_tickets = [construir_ticket_data(t, p, fases, repo) for t, p in tickets]
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    payload = {"generado": ahora, "repo": repo, "fases": fases, "tickets": datos_tickets}
    datos_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return HTML_TEMPLATE.replace("__DATOS_JSON__", datos_json)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DeployGo — Panel de gobernanza</title>
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --blue: #2f81f7;
    --green: #3fb950;
    --orange: #d29922;
    --red: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 32px 20px 60px;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  h1 { font-size: 24px; margin: 0 0 6px; }
  .subtitle { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
  .search { display: flex; gap: 10px; margin-bottom: 24px; }
  .search input {
    flex: 1; background: var(--card); border: 1px solid var(--border);
    color: var(--text); border-radius: 6px; padding: 10px 12px; font-size: 14px;
  }
  .search button {
    background: var(--blue); color: #fff; border: none; border-radius: 6px;
    padding: 0 18px; font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .search button:hover { opacity: .9; }
  .hint { color: var(--muted); font-size: 12px; margin: -14px 0 24px; }
  .hint button {
    background: none; border: none; color: var(--blue); cursor: pointer;
    font-size: 12px; padding: 0; text-decoration: underline;
  }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 18px; margin-bottom: 16px;
  }
  .header-card { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .header-card h2 { margin: 0 0 4px; font-size: 18px; }
  .meta { color: var(--muted); font-size: 13px; }
  .warn { color: var(--orange); font-size: 13px; }
  .badge {
    display: inline-block; background: rgba(47,129,247,.15); color: var(--blue);
    border: 1px solid rgba(47,129,247,.4); border-radius: 999px; padding: 4px 12px;
    font-size: 12px; font-weight: 600; white-space: nowrap;
  }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 640px) { .grid2 { grid-template-columns: 1fr; } }
  .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
  .progress-row { display: flex; align-items: center; gap: 12px; }
  .progress-track { flex: 1; height: 8px; background: var(--border); border-radius: 999px; overflow: hidden; }
  .progress-fill { height: 100%; background: var(--blue); border-radius: 999px; }
  .progress-pct { color: var(--blue); font-weight: 700; font-size: 14px; min-width: 42px; text-align: right; }
  .section-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; margin: 28px 0 10px; }
  .pipe-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 640px) { .pipe-grid { grid-template-columns: 1fr; } }
  .pipe-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .pipe-name { font-weight: 700; margin-bottom: 6px; }
  .pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; padding: 3px 9px; border-radius: 999px; margin-bottom: 8px; }
  .pill-success { background: rgba(63,185,80,.15); color: var(--green); }
  .pill-failure { background: rgba(248,81,73,.15); color: var(--red); }
  .pill-encurso { background: rgba(210,153,34,.15); color: var(--orange); }
  .pill-otro { background: rgba(139,148,158,.15); color: var(--muted); }
  .pipe-card .meta { margin-bottom: 6px; }
  .pipe-card a { color: var(--blue); text-decoration: none; }
  .pipe-card a:hover { text-decoration: underline; }
  .punto-actual { font-weight: 700; font-size: 13px; margin: 8px 0; }
  .toggle-link { background: none; border: none; color: var(--blue); cursor: pointer; font-size: 12.5px; padding: 0; margin-top: 4px; }
  .jobs-detail { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px; display: none; }
  .jobs-detail.open { display: block; }
  .job-row { font-size: 12.5px; margin-bottom: 6px; }
  .job-row .job-title { font-weight: 600; }
  .step-row { color: var(--muted); font-size: 12px; margin-left: 12px; }
  .timeline { position: relative; padding-left: 26px; }
  .fase-item { position: relative; padding-bottom: 22px; }
  .fase-item:last-child { padding-bottom: 0; }
  .fase-item::before {
    content: ""; position: absolute; left: -20px; top: 4px; bottom: -18px; width: 2px; background: var(--border);
  }
  .fase-item:last-child::before { display: none; }
  .fase-item.done::before, .fase-item.current::before { background: var(--blue); }
  .fase-dot {
    position: absolute; left: -26px; top: 2px; width: 12px; height: 12px; border-radius: 50%;
    background: var(--border); border: 2px solid var(--border);
  }
  .fase-item.done .fase-dot { background: var(--blue); border-color: var(--blue); }
  .fase-item.current .fase-dot { background: var(--bg); border-color: var(--blue); border-width: 3px; }
  .fase-nombre { font-weight: 700; font-size: 14.5px; }
  .fase-status { color: var(--muted); font-size: 12.5px; }
  .fase-detalle { margin-top: 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; font-size: 13px; }
  .fase-detalle p { margin: 0 0 8px; }
  .fase-detalle p:last-child { margin-bottom: 0; }
  .footer-link { margin-top: 20px; }
  .footer-link button { background: none; border: none; color: var(--blue); cursor: pointer; font-size: 13px; padding: 0; }
  table.pipe-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 20px; display: none; }
  table.pipe-table.open { display: table; }
  table.pipe-table th, table.pipe-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  table.pipe-table th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; }
  table.pipe-table a { color: var(--blue); text-decoration: none; }
  .footer-note { color: var(--muted); font-size: 11.5px; text-align: center; margin-top: 40px; }
  .not-found { color: var(--muted); padding: 20px 0; }
</style>
</head>
<body>
<div class="wrap">
  <h1>DeployGo — Panel de gobernanza</h1>
  <p class="subtitle">Traza auditable de un release: spec → código → tests → seguridad → aprobación → evidencia, cruzando Jira y GitHub Actions.</p>

  <div class="search">
    <input id="buscador" type="text" placeholder="Ej. SCRUM-14" autocomplete="off">
    <button id="btn-consultar">Consultar</button>
  </div>
  <p class="hint" id="hint"></p>

  <div id="panel"></div>
</div>

<script>
const DATA = __DATOS_JSON__;

const ticketsPorClave = {};
DATA.tickets.forEach(function (t) { ticketsPorClave[t.ticket.toUpperCase()] = t; });

const panel = document.getElementById("panel");
const buscador = document.getElementById("buscador");
const hint = document.getElementById("hint");

function claves() {
  return DATA.tickets.map(function (t) { return t.ticket; });
}

hint.innerHTML = "Tickets con spec declarado: " + claves().map(function (k) {
  return '<button onclick="consultar(\\'' + k + '\\')">' + k + "</button>";
}).join(" · ");

function pillClase(resultado) {
  if (resultado === "success") return "pill-success";
  if (resultado === "failure") return "pill-failure";
  if (resultado === "en curso") return "pill-encurso";
  return "pill-otro";
}

function pillIcono(resultado) {
  if (resultado === "success") return "✅";
  if (resultado === "failure") return "❌";
  if (resultado === "en curso") return "⏳";
  return "⚪";
}

function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderPipelineCard(p, idx) {
  var detalleHtml = "";
  var puntoHtml = "";
  if (p.detalle) {
    puntoHtml = '<div class="punto-actual">' + esc(p.detalle.punto_actual) + "</div>";
    var jobsHtml = p.detalle.jobs.map(function (job) {
      var steps = job.steps.map(function (s) {
        return '<div class="step-row">' + pillIcono(s.resultado === "—" ? job.estado : s.resultado) + " " + esc(s.step) + " — " + esc(s.resultado) + "</div>";
      }).join("");
      return '<div class="job-row"><div class="job-title">' + esc(job.job) + " — " + esc(job.resultado) + "</div>" + steps + "</div>";
    }).join("");
    detalleHtml = '<button class="toggle-link" onclick="toggleJobs(' + idx + ')">▸ Ver jobs y steps</button>' +
      '<div class="jobs-detail" id="jobs-' + idx + '">' + jobsHtml + "</div>";
  }
  return '<div class="pipe-card">' +
    '<div class="pipe-name">' + esc(p.workflow) + "</div>" +
    '<div class="pill ' + pillClase(p.resultado) + '">' + pillIcono(p.resultado) + " " + esc(p.resultado === "en curso" ? "En curso" : (p.resultado.charAt(0).toUpperCase() + p.resultado.slice(1))) + "</div>" +
    '<div class="meta">Rama: ' + esc(p.rama) + "</div>" +
    '<div class="meta">' + esc(p.ejecutado) + ' · <a href="' + p.url + '" target="_blank" rel="noopener">ver en GitHub ↗</a></div>' +
    puntoHtml + detalleHtml +
    "</div>";
}

function renderPipelineTable(pipelines) {
  if (!pipelines.length) return "";
  var filas = pipelines.map(function (p) {
    return "<tr><td>" + esc(p.workflow) + "</td><td>" + pillIcono(p.resultado) + " " + esc(p.resultado) + "</td><td>" + esc(p.rama) + "</td><td>" + esc(p.ejecutado) + '</td><td><a href="' + p.url + '" target="_blank" rel="noopener">Ver</a></td></tr>';
  }).join("");
  return '<table class="pipe-table" id="pipe-table">' +
    "<thead><tr><th>Pipeline</th><th>Resultado</th><th>Rama</th><th>Cuándo</th><th></th></tr></thead>" +
    "<tbody>" + filas + "</tbody></table>" +
    '<div class="footer-link"><button onclick="toggleTabla()">▸ Ver pipelines como tabla</button></div>';
}

function renderTimeline(fases, indiceActual) {
  return '<div class="timeline">' + fases.map(function (f, i) {
    var estado = indiceActual === null ? "" : (i < indiceActual ? "done" : (i === indiceActual ? "current" : ""));
    var detalle = "";
    if (i === indiceActual) {
      detalle = '<div class="fase-detalle">' +
        "<p><strong>Qué significa:</strong> " + esc(f.description) + "</p>" +
        "<p><strong>Evidencia esperada:</strong> " + esc(f.evidence) + "</p>" +
        "<p><strong>Siguiente paso:</strong> " + esc(f.next_step) + "</p>" +
        "</div>";
    }
    return '<div class="fase-item ' + estado + '"><div class="fase-dot"></div>' +
      '<div class="fase-nombre">' + esc(f.phase) + '</div>' +
      '<div class="fase-status">' + esc(f.jira_status) + "</div>" +
      detalle + "</div>";
  }).join("") + "</div>";
}

function toggleJobs(idx) {
  document.getElementById("jobs-" + idx).classList.toggle("open");
}

function toggleTabla() {
  var t = document.getElementById("pipe-table");
  if (t) t.classList.toggle("open");
}

function render(ticket) {
  var t = ticketsPorClave[(ticket || "").toUpperCase().trim()];
  if (!t) {
    panel.innerHTML = '<div class="not-found">No se encontró spec declarado para "' + esc(ticket) +
      '". Prueba con alguno de: ' + claves().join(", ") + ".</div>";
    return;
  }

  var jiraHtml = t.jira
    ? '<div class="meta">Asignado a ' + esc(t.jira.asignado_a) + " · actualizado " + esc(t.jira.actualizado) + "</div>"
    : '<div class="warn">⚠️ ' + esc(t.jira_error) + "</div>";

  var badgeHtml = t.jira ? '<span class="badge">' + esc(t.jira.estado) + "</span>" : "";

  var progresoHtml = "";
  if (t.fase_indice !== null) {
    var pct = Math.round(((t.fase_indice + 1) / t.fases_total) * 100);
    progresoHtml = '<div class="card"><div class="label">Avance del proceso (fase ' + (t.fase_indice + 1) + " de " + t.fases_total + ")</div>" +
      '<div class="progress-row"><div class="progress-track"><div class="progress-fill" style="width:' + pct + '%"></div></div>' +
      '<div class="progress-pct">' + pct + "%</div></div></div>";
  }

  var faseCardsHtml = "";
  if (t.fase) {
    faseCardsHtml = '<div class="grid2">' +
      '<div class="card"><div class="label">Fase del proceso</div>' +
      '<div style="font-weight:700;font-size:15px;">' + esc(t.fase.phase) + "</div>" +
      '<div class="meta">' + esc(t.fase.jira_status) + "</div></div>" +
      '<div class="card"><div class="label">Siguiente paso</div>' +
      '<div style="font-weight:700;font-size:14px;">' + esc(t.fase.next_step) + "</div></div>" +
      "</div>";
  }

  var pipelinesHtml = t.pipelines.length
    ? '<div class="pipe-grid">' + t.pipelines.map(renderPipelineCard).join("") + "</div>" + renderPipelineTable(t.pipelines)
    : '<div class="meta">— sin ejecuciones encontradas —</div>';

  var timelineHtml = DATA.fases.length ? renderTimeline(DATA.fases, t.fase_indice) : "";

  var contratoHtml = t.contrato
    ? '<div class="card"><div class="label">Contrato del cambio</div><div class="meta">' + esc(t.contrato) + "</div></div>"
    : "";

  panel.innerHTML =
    '<div class="card header-card"><div><h2>' + esc(t.ticket) + " — " + esc(t.titulo) + "</h2>" + jiraHtml + "</div>" + badgeHtml + "</div>" +
    faseCardsHtml + progresoHtml +
    '<div class="section-label">Pipelines</div>' + pipelinesHtml +
    '<div class="section-label">Traza del proceso</div><div class="card">' + timelineHtml + "</div>" +
    contratoHtml +
    '<div class="footer-note">Ver PRs relacionados: <a href="' + t.prs_url + '" target="_blank" rel="noopener" style="color:var(--blue)">' + t.prs_url + "</a></div>";
}

function consultar(valor) {
  buscador.value = valor;
  render(valor);
}

document.getElementById("btn-consultar").addEventListener("click", function () { render(buscador.value); });
buscador.addEventListener("keydown", function (e) { if (e.key === "Enter") render(buscador.value); });

if (DATA.tickets.length) {
  consultar(DATA.tickets[0].ticket);
}

document.title = "DeployGo — Panel de gobernanza";
</script>

<p class="footer-note">Generado automáticamente el __GENERADO__ · Fuentes: Jira + GitHub Actions · los % de avance ubican la fase dentro del proceso, no miden esfuerzo real.</p>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="Archivo de salida para el Markdown (por defecto: stdout)")
    ap.add_argument("--out-html", help="Archivo de salida para el panel HTML")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        logger.warning("GITHUB_REPO no está definido; los enlaces a PRs quedarán rotos.")

    if args.out or not args.out_html:
        contenido_md = generar_markdown(repo)
        if args.out:
            carpeta = os.path.dirname(args.out)
            if carpeta:
                os.makedirs(carpeta, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(contenido_md)
            logger.info("Dashboard (Markdown) escrito en %s", args.out)
        else:
            print(contenido_md)

    if args.out_html:
        contenido_html = generar_html(repo)
        contenido_html = contenido_html.replace(
            "__GENERADO__",
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        carpeta = os.path.dirname(args.out_html)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        with open(args.out_html, "w", encoding="utf-8") as f:
            f.write(contenido_html)
        logger.info("Dashboard (HTML) escrito en %s", args.out_html)


if __name__ == "__main__":
    main()
