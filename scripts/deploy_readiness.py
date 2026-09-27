#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_readiness.py — Deploy Readiness Agent (Fase 4 del SDLC agentico: advisory visible).

Antes de correr CICD-CERT, cruza Jira, el historial de pipelines, el spec del
ticket y deploy-policy.yml, y le pide a Claude un veredicto de lectura sobre
si el ticket esta listo para certificar. A diferencia de deploy-policy.yml
(scripts/policy.py), que es un chequeo deterministico de rama + estado exacto
de Jira, este agente puede razonar sobre matices (ej: "Jira dice Done pero el
ultimo pipeline de DEV fallo", o "el spec pide un flag de feature que no
aparece en el diff"). Ver agents/deploy-readiness.yml para el manifest.

ADVISORY VISIBLE (Fase 4): ademas de quedar en agent-log.jsonl (via
agent_log.instrumentar, igual que en Fase 3), el veredicto se escribe en el
resumen del job de GitHub Actions ($GITHUB_STEP_SUMMARY) -- visible para quien
dispare o revise esa corrida de CICD-CERT. Sigue sin comentar en Jira, sin
comentar en el PR y sin bloquear nada (el step que lo llama mantiene
continue-on-error: true): es informacion para que una persona decida, no una
aprobacion ni un bloqueo automatico.

Uso (pensado para correr como step de cicd-cert.yml, con continue-on-error):
    python3 scripts/deploy_readiness.py <TICKET>
"""

import logging
import os
import re
import sys

from anthropic import Anthropic

import agent_log
import tools

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """Sos el Deploy Readiness Agent de un equipo de DevSecOps.

Tu trabajo es leer el contexto de un ticket (Jira, pipelines recientes, spec
declarado y la politica deploy-policy.yml) y dar un veredicto de lectura sobre
si el ticket esta listo para promover a certificacion (CICD-CERT).

Reglas importantes:
- NO repitas el chequeo deterministico que ya hace deploy-policy.yml (rama
  correcta + estado exacto de Jira). Eso ya se evaluo antes que vos corras.
  Tu valor esta en detectar señales adicionales: pipelines de DEV que
  fallaron o quedaron pendientes, un spec que no coincide con lo que
  describe el ticket, comentarios de Jira que sugieren que el trabajo no
  esta terminado, o inconsistencias entre las fuentes.
- Este es un chequeo de LECTURA solamente. Nunca aprobás ni bloqueás nada de
  verdad -- tu veredicto hoy solo queda en un log interno.
- Respondé siempre en español.
- La PRIMERA linea de tu respuesta tiene que ser exactamente una de:
    VEREDICTO: LISTO
    VEREDICTO: NO_LISTO
- Despues de esa primera linea, hasta 4 bullets cortos explicando por que.
  Si no encontras nada preocupante, decilo brevemente igual.
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_VEREDICTO_RE = re.compile(r"^VEREDICTO:\s*(LISTO|NO_LISTO)\b", re.MULTILINE)


def _armar_contexto(ticket):
    # Llamadas directas (bare), no via tools.TOOL_FUNCTIONS: evita que
    # agent_log._agente_de_tool le atribuya estas lecturas a otro agente que
    # tambien declare la misma tool (ver nota en agents/deploy-readiness.yml).
    jira = tools.consultar_jira(ticket)
    pipelines = tools.consultar_pipelines(ticket=ticket)
    spec = tools.consultar_spec(ticket)
    politica = tools.consultar_politica("deploy-policy")
    return jira, pipelines, spec, politica


def _evaluar_readiness_impl(ticket):
    jira, pipelines, spec, politica = _armar_contexto(ticket)
    mensaje = (
        f"TICKET: {ticket}\n\n"
        f"JIRA:\n{jira}\n\n"
        f"PIPELINES RECIENTES:\n{pipelines}\n\n"
        f"SPEC:\n{spec}\n\n"
        f"DEPLOY-POLICY:\n{politica}\n"
    )
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": mensaje}],
        )
        texto = "".join(b.text for b in resp.content if b.type == "text")
    except Exception as e:
        logger.error("deploy_readiness: fallo llamando a la API de Anthropic: %s", e)
        return {"veredicto": "ERROR", "razonamiento": f"{type(e).__name__}: {e}", "datos": mensaje}

    m = _VEREDICTO_RE.search(texto)
    veredicto = m.group(1) if m else "ERROR"
    if not m:
        logger.warning("deploy_readiness: respuesta sin el formato esperado: %r", texto[:200])
    return {"veredicto": veredicto, "razonamiento": texto, "datos": mensaje}


# Unica funcion instrumentada: cada corrida deja UNA linea limpia en
# agent-log.jsonl bajo agente_id="deploy-readiness", sin duplicar logs por
# cada lectura interna (consultar_jira, consultar_pipelines, etc. se llaman
# bare arriba, sin pasar por TOOL_FUNCTIONS).
evaluar_readiness = agent_log.instrumentar("evaluar_readiness", _evaluar_readiness_impl)


_INSIGNIA = {
    "LISTO": "\u2705 LISTO",
    "NO_LISTO": "\u26a0\ufe0f NO\u00a0LISTO",
    "ERROR": "\u2757 ERROR",
}


def _escribir_resumen(ticket, resultado):
    """Escribe el veredicto en el resumen del job de GitHub Actions
    ($GITHUB_STEP_SUMMARY), si esa variable existe (no esta seteada en una
    corrida local, asi que ahi simplemente no hace nada). Puramente
    informativo -- no escribe en ningun otro sistema (ver agents/deploy-readiness.yml
    sobre por que no comenta en Jira)."""
    resumen_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not resumen_path:
        return

    insignia = _INSIGNIA.get(resultado["veredicto"], resultado["veredicto"])
    cuerpo = resultado["razonamiento"]

    texto = (
        f"### \U0001f9ed Deploy Readiness Agent -- {ticket}\n\n"
        f"**Veredicto: {insignia}**\n\n"
        f"{cuerpo}\n\n"
        f"> Advisory -- no bloquea esta certificacion. El agente puede equivocarse; "
        f"la decision final es de quien certifica. Detalle completo en agent-log.jsonl "
        f"(artifact de este run).\n"
    )
    try:
        with open(resumen_path, "a", encoding="utf-8") as f:
            f.write(texto)
    except OSError as e:
        logger.warning("deploy_readiness: no se pudo escribir el resumen del job: %s", e)


def main():
    if len(sys.argv) < 2:
        print("Uso: deploy_readiness.py <ticket>")
        sys.exit(1)
    ticket = sys.argv[1]
    logger.info("deploy_readiness: evaluando %s (advisory visible -- Fase 4)", ticket)
    resultado = evaluar_readiness(ticket)
    _escribir_resumen(ticket, resultado)
    print(
        f"\u2139\ufe0f Deploy Readiness Agent para {ticket}: "
        f"veredicto={resultado['veredicto']} (ver el resumen del job y agent-log.jsonl)"
    )


if __name__ == "__main__":
    main()
