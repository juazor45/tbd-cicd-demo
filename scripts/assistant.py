#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assistant.py — DeployGo Assistant (prototipo agéntico)

Chat en lenguaje natural sobre el estado de los releases. El agente decide
qué herramientas invocar (Jira, GitHub Actions, template del proceso) y
responde explicando dónde está el release y cuál es el siguiente paso.

Uso:
  pip install anthropic
  python assistant.py                          # modo conversacional
  python assistant.py "¿en qué va SCRUM-10?"   # pregunta única

Variables de entorno:
  ANTHROPIC_API_KEY, JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN,
  GITHUB_REPO (owner/repo), GITHUB_TOKEN (opcional en repos públicos),
  SSL_NO_VERIFY=1 (solo si tu Windows corporativo falla con certificados)
"""

import json
import os
import sys

from anthropic import Anthropic
from tools import TOOL_SCHEMAS, TOOL_FUNCTIONS

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """Eres el DeployGo Assistant, el asistente del proceso de entrega de software del equipo.

Tu trabajo es responder, en español y de forma clara y breve, dónde está un release y qué debe hacer el usuario a continuación.

Cómo trabajas:
- Cuando pregunten por un release o ticket, consulta Jira Y los pipelines antes de responder: el estado real surge de cruzar ambos.
- Si un pipeline no está en "success", usa detalle_ejecucion con su run_id para decir exactamente en qué job y step se quedó.
- Consulta el template del proceso para ubicar la fase y determinar el siguiente paso; no inventes pasos que no estén ahí.
- Si Jira y los pipelines se contradicen (por ejemplo, el ticket avanzado pero el pipeline fallido), señálalo explícitamente: es justo el tipo de inconsistencia que el usuario necesita saber.

Estilo de respuesta:
- Empieza por la conclusión: en qué fase está y qué sigue.
- Añade solo los datos que sustentan esa conclusión (estado del ticket, pipeline relevante, step donde se detuvo).
- Termina con el siguiente paso concreto y accionable.
- No repitas volcados de datos crudos ni listes todo lo que consultaste.
- Si falta información o una herramienta devuelve error, dilo con claridad en vez de suponer.
"""


def ejecutar_tool(nombre, args):
    fn = TOOL_FUNCTIONS.get(nombre)
    if not fn:
        return {"error": f"Herramienta desconocida: {nombre}"}
    try:
        return fn(**args)
    except Exception as e:
        return {"error": f"Fallo ejecutando {nombre}: {e}"}


def preguntar(client, mensajes):
    """Loop agéntico: el modelo pide tools, las ejecutamos, hasta la respuesta final."""
    while True:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=mensajes,
        )
        mensajes.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            texto = "".join(b.text for b in resp.content if b.type == "text")
            return texto, mensajes

        resultados = []
        for bloque in resp.content:
            if bloque.type == "tool_use":
                print(f"   🔧 consultando: {bloque.name}({json.dumps(bloque.input, ensure_ascii=False)})")
                salida = ejecutar_tool(bloque.name, bloque.input)
                resultados.append({
                    "type": "tool_result",
                    "tool_use_id": bloque.id,
                    "content": json.dumps(salida, ensure_ascii=False),
                })
        mensajes.append({"role": "user", "content": resultados})


def verificar_entorno():
    faltantes = [v for v in ("ANTHROPIC_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL",
                             "JIRA_API_TOKEN", "GITHUB_REPO") if not os.environ.get(v)]
    if faltantes:
        print(f"❌ Faltan variables de entorno: {', '.join(faltantes)}")
        sys.exit(1)


def main():
    verificar_entorno()
    client = Anthropic()
    mensajes = []

    if len(sys.argv) > 1:
        pregunta = " ".join(sys.argv[1:])
        mensajes.append({"role": "user", "content": pregunta})
        respuesta, _ = preguntar(client, mensajes)
        print(f"\n{respuesta}\n")
        return

    print("=" * 68)
    print("🤖 DeployGo Assistant — pregunta por el estado de tus releases")
    print("   Ejemplos: '¿en qué va SCRUM-10?' | '¿por qué falló el pipeline?'")
    print("   Escribe 'salir' para terminar.")
    print("=" * 68)

    while True:
        try:
            pregunta = input("\n👤 Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Hasta luego.")
            return
        if pregunta.lower() in ("salir", "exit", "quit", ""):
            print("👋 Hasta luego.")
            return
        mensajes.append({"role": "user", "content": pregunta})
        respuesta, mensajes = preguntar(client, mensajes)
        print(f"\n🤖 Assistant: {respuesta}")


if __name__ == "__main__":
    main()
