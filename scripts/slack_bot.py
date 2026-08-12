#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slack_bot.py — DeployGo Assistant en Slack.

Usa Socket Mode: el bot abre una conexión SALIENTE hacia Slack, así que
NO necesita URL pública, HTTPS ni exponer puertos. Ideal para correr
desde una laptop o un runner interno.

Formas de invocarlo en Slack:
  /release SCRUM-10              → slash command
  @DeployGo ¿en qué va SCRUM-10? → mención en un canal
  mensaje directo al bot         → conversación privada

Cada hilo (thread) mantiene su propio historial, así el equipo puede
tener varias consultas en paralelo sin mezclarlas.

Ejecutar:
    pip install slack-bolt anthropic
    python slack_bot.py
"""

import json
import os
import re

from anthropic import Anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from tools import TOOL_SCHEMAS, TOOL_FUNCTIONS

MODEL = "claude-sonnet-4-6"
MAX_ITER = 8

SYSTEM_PROMPT = """Eres el DeployGo Assistant, el asistente del proceso de entrega de software del equipo, respondiendo dentro de Slack.

Tu trabajo es decir, en español y de forma breve, dónde está un release y qué debe hacer el usuario a continuación.

Cómo trabajas:
- Cuando pregunten por un release o ticket, consulta Jira Y los pipelines antes de responder: el estado real surge de cruzar ambos.
- Al consultar pipelines por un ticket concreto, pasa el parámetro 'ticket' para ver solo las ejecuciones de ese release. Si no devuelve ninguna, vuelve a consultar sin filtro y aclara que las ejecuciones mostradas son las últimas del repositorio, no necesariamente de ese ticket.
- Si un pipeline no está en "success", usa detalle_ejecucion con su run_id para decir en qué job y step se quedó.
- Consulta el template del proceso para ubicar la fase y el siguiente paso; no inventes pasos que no estén ahí.
- Si Jira y los pipelines se contradicen, señálalo explícitamente.

Formato Slack (importante):
- Usa *negrita* con un asterisco (no doble), `código` con backticks, y viñetas con •.
- Máximo 6 líneas. Estás en un chat de equipo, no escribiendo un informe.
- Empieza por la conclusión, cierra con el siguiente paso concreto.
"""

app = App(token=os.environ["SLACK_BOT_TOKEN"])
client = Anthropic()

# Historial por hilo: {thread_ts: [mensajes]}
_hilos: dict[str, list] = {}


def _ejecutar_tool(nombre, args):
    fn = TOOL_FUNCTIONS.get(nombre)
    if not fn:
        return {"error": f"Herramienta desconocida: {nombre}"}
    try:
        return fn(**args)
    except Exception as e:
        return {"error": f"Fallo ejecutando {nombre}: {e}"}


def consultar_agente(pregunta, hilo_id, on_tool=None):
    """Loop agéntico. on_tool(nombre, args) permite mostrar progreso en Slack."""
    mensajes = _hilos.setdefault(hilo_id, [])
    mensajes.append({"role": "user", "content": pregunta})

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
            if b.type == "tool_use":
                if on_tool:
                    on_tool(b.name, b.input)
                resultados.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": json.dumps(_ejecutar_tool(b.name, b.input), ensure_ascii=False),
                })
        mensajes.append({"role": "user", "content": resultados})

    return "⚠️ El agente excedió el número de consultas permitidas."


def responder(say, client_slack, canal, hilo, pregunta):
    """Publica un mensaje de progreso, lo va actualizando y lo reemplaza con la respuesta."""
    placeholder = say(text="_Consultando Jira y GitHub…_", thread_ts=hilo)
    ts = placeholder["ts"]
    pasos = []

    def on_tool(nombre, args):
        pasos.append(f"• `{nombre}({', '.join(str(v) for v in args.values())})`")
        try:
            client_slack.chat_update(
                channel=canal, ts=ts,
                text="_Consultando…_\n" + "\n".join(pasos[-4:]),
            )
        except Exception:
            pass  # el progreso es cosmético; nunca debe romper la respuesta

    try:
        texto = consultar_agente(pregunta, hilo, on_tool)
    except Exception as e:
        texto = f"⚠️ Error del asistente: `{e}`"

    client_slack.chat_update(channel=canal, ts=ts, text=texto)


# ----------------------------------------------------------------------
# Entradas: slash command, mención y mensaje directo
# ----------------------------------------------------------------------
@app.command("/release")
def cmd_release(ack, command, say, client):
    ack()
    texto = (command.get("text") or "").strip()
    if not texto:
        say("Uso: `/release SCRUM-10` o `/release ¿algún pipeline falló?`")
        return
    # Si solo mandan la key, la convertimos en pregunta natural
    if re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", texto, re.IGNORECASE):
        texto = f"¿En qué va {texto.upper()}?"
    responder(say, client, command["channel_id"], None, texto)


@app.event("app_mention")
def on_mention(event, say, client):
    pregunta = re.sub(r"<@[^>]+>", "", event["text"]).strip()
    if not pregunta:
        say("Pregúntame algo como: `¿en qué va SCRUM-10?`", thread_ts=event["ts"])
        return
    hilo = event.get("thread_ts") or event["ts"]
    responder(say, client, event["channel"], hilo, pregunta)


@app.event("message")
def on_dm(event, say, client):
    # Solo mensajes directos de personas (ignora bots y mensajes de canal)
    if event.get("channel_type") != "im" or event.get("bot_id"):
        return
    hilo = event.get("thread_ts") or event["ts"]
    responder(say, client, event["channel"], hilo, event.get("text", ""))


if __name__ == "__main__":
    faltan = [v for v in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ANTHROPIC_API_KEY",
                          "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "GITHUB_REPO")
              if not os.environ.get(v)]
    if faltan:
        raise SystemExit(f"❌ Faltan variables de entorno: {', '.join(faltan)}")
    print("🚀 DeployGo Assistant conectado a Slack (Socket Mode). Ctrl+C para detener.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
