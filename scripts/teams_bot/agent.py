#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py — El loop agéntico del DeployGo Assistant, portado de scripts/slack_bot.py.

Misma lógica exacta que consultar_agente()/_ejecutar_tool() en el bot de Slack:
mismo modelo, mismo MAX_ITER, mismas 5 tools (importadas de tools.py sin
tocarlas), mismo criterio de historial por hilo. Lo único que cambia es el
formato de texto del SYSTEM_PROMPT: mrkdwn de Slack (*negrita*, backticks,
viñetas •) se reemplaza por Markdown estándar (**negrita**), que es lo que
Teams/Bot Framework renderiza en Adaptive Cards y en texto plano de chat.
"""

import json
import logging

from anthropic import Anthropic

from state import historial, MAX_MENSAJES_POR_HILO
from tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

logger = logging.getLogger(__name__)

client = Anthropic()  # lee ANTHROPIC_API_KEY del entorno

MODEL = "claude-sonnet-4-6"
MAX_ITER = 8

SYSTEM_PROMPT = """Eres el DeployGo Assistant, el asistente del proceso de entrega de software del equipo, respondiendo dentro de Microsoft Teams.

Tu trabajo es decir, en español y de forma breve, dónde está un release y qué debe hacer el usuario a continuación.

Cómo trabajas:
- Cuando pregunten por un release o ticket, consulta Jira Y los pipelines antes de responder: el estado real surge de cruzar ambos.
- Al consultar pipelines por un ticket concreto, pasa el parámetro 'ticket' para ver solo las ejecuciones de ese release. Si no devuelve ninguna, vuelve a consultar sin filtro y aclara que las ejecuciones mostradas son las últimas del repositorio, no necesariamente de ese ticket.
- Si un pipeline no está en "success", usa detalle_ejecucion con su run_id para decir en qué job y step se quedó.
- Consulta el template del proceso para ubicar la fase y el siguiente paso; no inventes pasos que no estén ahí.
- Si preguntan por el alcance, los límites o los criterios de aceptación de un ticket, usa consultar_spec.
- Si Jira y los pipelines se contradicen, señálalo explícitamente.

Formato Teams (importante):
- Usa **negrita** con doble asterisco, `código` con backticks, y viñetas con -.
- Máximo 6 líneas. Estás en un chat de equipo, no escribiendo un informe.
- Empieza por la conclusión, cierra con el siguiente paso concreto.
"""


def _ejecutar_tool(nombre, args):
    fn = TOOL_FUNCTIONS.get(nombre)
    if not fn:
        return {"error": f"Herramienta desconocida: {nombre}"}
    try:
        return fn(**args)
    except Exception as e:  # nunca dejar que un fallo de tool rompa el loop
        logger.exception("Fallo ejecutando tool %s", nombre)
        return {"error": f"Fallo ejecutando {nombre}: {e}"}


def consultar_agente(pregunta, hilo_id, on_tool=None):
    """Igual que en Slack: hasta MAX_ITER vueltas de tool_use antes de rendirse.

    on_tool(nombre, args), si se pasa, se invoca por cada tool que el agente
    decide ejecutar -- se usa para ir mostrando progreso mientras responde.
    """
    mensajes = historial(hilo_id)
    mensajes.append({"role": "user", "content": pregunta})
    if len(mensajes) > MAX_MENSAJES_POR_HILO:
        del mensajes[: len(mensajes) - MAX_MENSAJES_POR_HILO]

    for _ in range(MAX_ITER):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=mensajes,
        )
        mensajes.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")

        resultados = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            if on_tool:
                on_tool(b.name, b.input)
            resultado = _ejecutar_tool(b.name, b.input)
            resultados.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )
        mensajes.append({"role": "user", "content": resultados})

    return "⚠️ El agente excedió el número de consultas permitidas."
