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
  /crear-ticket                  → abre un formulario para crear un issue en Jira

Cada hilo (thread) mantiene su propio historial, así el equipo puede
tener varias consultas en paralelo sin mezclarlas.

/crear-ticket es deliberadamente distinto al resto: no pasa por el agente de
IA ni por texto libre. Es un formulario (modal de Slack) con confirmación
explícita antes de escribir en Jira, restringido a un canal designado
(JIRA_TICKET_CHANNEL_ID). Ver la sección "Creación de tickets" más abajo.

Ejecutar:
    pip install slack-bolt anthropic
    python slack_bot.py
"""

import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict, deque

from anthropic import Anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from tools import (
    MAX_DESCRIPCION, MAX_TITULO, TOOL_FUNCTIONS, TOOL_SCHEMAS,
    comentar_ticket, crear_ticket, listar_tipos_issue,
)

MODEL = "claude-sonnet-4-6"
MAX_ITER = 8
MAX_HILOS = 200              # límite de hilos activos en memoria (evita crecimiento indefinido)
MAX_MENSAJES_POR_HILO = 40   # recorta el historial de un hilo muy largo
RATE_LIMIT_MAX = 6           # máximo de preguntas...
RATE_LIMIT_WINDOW_S = 60     # ...por usuario, cada N segundos
MAX_USUARIOS_RATE_LIMIT = 500  # tope de usuarios trackeados en memoria

# /crear-ticket: canal donde se permite el comando, y proyecto de Jira fijo
# (no editable desde el formulario -- evita crear issues en proyectos no
# previstos). Si cualquiera de los dos falta, el comando se desactiva solo,
# con un mensaje claro, en vez de que el proceso truene al arrancar.
JIRA_TICKET_CHANNEL_ID = os.environ.get("JIRA_TICKET_CHANNEL_ID", "")
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "")
MAX_BORRADORES = 200  # tope de confirmaciones de ticket pendientes en memoria

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deploygo-slack-bot")

SYSTEM_PROMPT = """Eres el DeployGo Assistant, el asistente del proceso de entrega de software del equipo, respondiendo dentro de Slack.

Tu trabajo es decir, en español y de forma breve, dónde está un release y qué debe hacer el usuario a continuación.

Cómo trabajas:
- Cuando pregunten por un release o ticket, consulta Jira Y los pipelines antes de responder: el estado real surge de cruzar ambos.
- Al consultar pipelines por un ticket concreto, pasa el parámetro 'ticket' para ver solo las ejecuciones de ese release. Si no devuelve ninguna, vuelve a consultar sin filtro y aclara que las ejecuciones mostradas son las últimas del repositorio, no necesariamente de ese ticket.
- Si un pipeline no está en "success", usa detalle_ejecucion con su run_id para decir en qué job y step se quedó.
- Consulta el template del proceso para ubicar la fase y el siguiente paso; no inventes pasos que no estén ahí.
- Si preguntan por el alcance, los límites o los criterios de aceptación de un ticket, usa consultar_spec.
- Si Jira y los pipelines se contradicen, señálalo explícitamente.

Formato Slack (importante):
- Usa *negrita* con un asterisco (no doble), `código` con backticks, y viñetas con •.
- Máximo 6 líneas. Estás en un chat de equipo, no escribiendo un informe.
- Empieza por la conclusión, cierra con el siguiente paso concreto.
"""

app = App(token=os.environ["SLACK_BOT_TOKEN"])
client = Anthropic()

# Historial por hilo: {thread_ts: [mensajes]}. OrderedDict con límite de tamaño
# para no crecer sin control en un proceso de larga duración (el bot no persiste
# estado entre reinicios; esto solo acota la memoria mientras corre).
_hilos: "OrderedDict[str, list]" = OrderedDict()


# Ventanas de rate limit por usuario: {user_id: deque(timestamps)}. Igual que
# _hilos, acotado en tamaño para no crecer sin control.
_rate_limit: "OrderedDict[str, deque]" = OrderedDict()


def _rate_limited(usuario_id):
    """True si el usuario superó RATE_LIMIT_MAX consultas en los últimos RATE_LIMIT_WINDOW_S segundos."""
    if not usuario_id:
        return False
    ahora = time.monotonic()
    if usuario_id in _rate_limit:
        _rate_limit.move_to_end(usuario_id)
    else:
        if len(_rate_limit) >= MAX_USUARIOS_RATE_LIMIT:
            _rate_limit.popitem(last=False)
        _rate_limit[usuario_id] = deque()
    marcas = _rate_limit[usuario_id]
    while marcas and ahora - marcas[0] > RATE_LIMIT_WINDOW_S:
        marcas.popleft()
    if len(marcas) >= RATE_LIMIT_MAX:
        return True
    marcas.append(ahora)
    return False


def _historial(hilo_id):
    """Devuelve (y marca como reciente) el historial de un hilo, descartando el más viejo si se excede MAX_HILOS."""
    if hilo_id in _hilos:
        _hilos.move_to_end(hilo_id)
        return _hilos[hilo_id]
    if len(_hilos) >= MAX_HILOS:
        viejo_id, _ = _hilos.popitem(last=False)
        logger.info("Descartando historial del hilo %s (límite de %d hilos activos)", viejo_id, MAX_HILOS)
    mensajes = []
    _hilos[hilo_id] = mensajes
    return mensajes


# ----------------------------------------------------------------------
# Creación de tickets: /crear-ticket
# ----------------------------------------------------------------------
# Estado efímero (vive solo mientras el proceso corre, igual que _hilos y
# _rate_limit): el modal de Slack solo puede llevar ~2000 caracteres por
# botón, así que en vez de meter titulo+descripcion en el botón de
# "Confirmar" guardamos el borrador acá y el botón solo lleva un token corto.
_borradores: "OrderedDict[str, dict]" = OrderedDict()


def _guardar_borrador(datos):
    token = uuid.uuid4().hex[:12]
    if len(_borradores) >= MAX_BORRADORES:
        _borradores.popitem(last=False)
    _borradores[token] = datos
    return token


# Los tipos de issue disponibles dependen del esquema de CADA proyecto de Jira
# (y de su idioma -- un proyecto puede no tener "Task", o llamarlo "Tarea").
# Se consultan una vez y se cachean en memoria mientras el proceso corre; si
# el esquema del proyecto cambia, hace falta reiniciar el bot para verlo
# reflejado (mismo criterio que el resto del estado en memoria de este archivo).
_tipos_cache = None


def _tipos_disponibles():
    global _tipos_cache
    if _tipos_cache is not None:
        return _tipos_cache
    resultado = listar_tipos_issue(JIRA_PROJECT_KEY)
    if "error" in resultado or not resultado.get("tipos"):
        logger.warning("No se pudieron leer los tipos de issue de %s: %s", JIRA_PROJECT_KEY, resultado.get("error"))
        return None  # no cacheamos el fallo: el proximo intento vuelve a consultar
    _tipos_cache = resultado["tipos"]
    return _tipos_cache


def _modal_crear_ticket(tipos):
    return {
        "type": "modal",
        "callback_id": "modal_crear_ticket",
        "title": {"type": "plain_text", "text": "Crear ticket"},
        "submit": {"type": "plain_text", "text": "Continuar"},
        "close": {"type": "plain_text", "text": "Cancelar"},
        "blocks": [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Proyecto: *{JIRA_PROJECT_KEY}* (fijo, no editable)"}],
            },
            {
                "type": "input",
                "block_id": "b_tipo",
                "label": {"type": "plain_text", "text": "Tipo"},
                "element": {
                    "type": "static_select",
                    "action_id": "tipo",
                    "options": [{"text": {"type": "plain_text", "text": t}, "value": t} for t in tipos],
                },
            },
            {
                "type": "input",
                "block_id": "b_titulo",
                "label": {"type": "plain_text", "text": "Título"},
                "element": {"type": "plain_text_input", "action_id": "titulo", "max_length": MAX_TITULO},
            },
            {
                "type": "input",
                "block_id": "b_descripcion",
                "label": {"type": "plain_text", "text": "Descripción"},
                "element": {
                    "type": "plain_text_input", "action_id": "descripcion",
                    "multiline": True, "max_length": MAX_DESCRIPCION,
                },
            },
        ],
    }


def _borrador_spec(ticket, titulo):
    """Genera un borrador de specs/<TICKET>.yml a partir de los datos ya
    capturados. Es un punto de partida para editar, no un spec terminado --
    cambios_permitidos/prohibidos y evidencia_requerida dependen del codigo
    real que se va a tocar, algo que el formulario de creacion no conoce."""
    titulo_esc = titulo.replace('"', "'")
    return f'''ticket: {ticket}
titulo: "{titulo_esc}"

cambios_permitidos:
  - "TODO: rutas que este cambio SI puede tocar, ej. src/main/java/.../paquete/**"

cambios_prohibidos:
  - "TODO: rutas que este cambio NO debe tocar, ej. .github/workflows/**"

contrato: >
  TODO: que comportamiento (de API, de datos, de lo que sea) tiene que
  preservarse o agregarse con este cambio.

evidencia_requerida:
  - "TODO: que prueba que el trabajo quedo hecho, ej. un test nuevo"
  - "CI (ci-pr.yml) en verde"
'''


def _ejecutar_tool(nombre, args):
    fn = TOOL_FUNCTIONS.get(nombre)
    if not fn:
        return {"error": f"Herramienta desconocida: {nombre}"}
    try:
        return fn(**args)
    except Exception as e:
        logger.exception("Fallo ejecutando tool %s(%s)", nombre, args)
        return {"error": f"Fallo ejecutando {nombre}: {e}"}


def consultar_agente(pregunta, hilo_id, on_tool=None):
    """Loop agéntico. on_tool(nombre, args) permite mostrar progreso en Slack."""
    mensajes = _historial(hilo_id)
    mensajes.append({"role": "user", "content": pregunta})
    if len(mensajes) > MAX_MENSAJES_POR_HILO:
        del mensajes[: len(mensajes) - MAX_MENSAJES_POR_HILO]
        logger.info("Historial del hilo %s recortado a %d mensajes", hilo_id, MAX_MENSAJES_POR_HILO)

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


def responder(say, client_slack, canal, hilo, pregunta, usuario=None):
    """Publica un mensaje de progreso, lo va actualizando y lo reemplaza con la respuesta."""
    if _rate_limited(usuario):
        logger.info("rate limit aplicado a usuario=%s", usuario)
        say(text="⏳ Estás consultando muy seguido. Espera un minuto e intenta de nuevo.", thread_ts=hilo)
        return
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
    except Exception:
        logger.exception("Error del asistente respondiendo en canal=%s hilo=%s", canal, hilo)
        texto = "⚠️ No pude completar la consulta. Intenta de nuevo en unos segundos."

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
    logger.info("slash /release user=%s canal=%s pregunta=%r", command.get("user_id"), command["channel_id"], texto)
    responder(say, client, command["channel_id"], None, texto, usuario=command.get("user_id"))


@app.event("app_mention")
def on_mention(event, say, client):
    pregunta = re.sub(r"<@[^>]+>", "", event["text"]).strip()
    if not pregunta:
        say("Pregúntame algo como: `¿en qué va SCRUM-10?`", thread_ts=event["ts"])
        return
    hilo = event.get("thread_ts") or event["ts"]
    logger.info("mención user=%s canal=%s hilo=%s pregunta=%r", event.get("user"), event["channel"], hilo, pregunta)
    responder(say, client, event["channel"], hilo, pregunta, usuario=event.get("user"))


@app.event("message")
def on_dm(event, say, client):
    # Solo mensajes directos de personas (ignora bots y mensajes de canal)
    if event.get("channel_type") != "im" or event.get("bot_id"):
        return
    hilo = event.get("thread_ts") or event["ts"]
    logger.info("DM user=%s hilo=%s pregunta=%r", event.get("user"), hilo, event.get("text", ""))
    responder(say, client, event["channel"], hilo, event.get("text", ""), usuario=event.get("user"))


@app.command("/crear-ticket")
def cmd_crear_ticket(ack, command, client):
    ack()
    usuario = command.get("user_id")
    canal = command["channel_id"]

    if not JIRA_TICKET_CHANNEL_ID or not JIRA_PROJECT_KEY:
        logger.warning("/crear-ticket invocado pero falta JIRA_TICKET_CHANNEL_ID o JIRA_PROJECT_KEY")
        client.chat_postEphemeral(channel=canal, user=usuario,
                                   text="⚠️ Este comando no está configurado todavía (falta JIRA_TICKET_CHANNEL_ID o JIRA_PROJECT_KEY).")
        return
    if canal != JIRA_TICKET_CHANNEL_ID:
        client.chat_postEphemeral(channel=canal, user=usuario,
                                   text=f"⚠️ /crear-ticket solo funciona en <#{JIRA_TICKET_CHANNEL_ID}>.")
        return
    if _rate_limited(usuario):
        client.chat_postEphemeral(channel=canal, user=usuario,
                                   text="⏳ Estás usando el comando muy seguido. Espera un minuto.")
        return

    tipos = _tipos_disponibles()
    if not tipos:
        client.chat_postEphemeral(channel=canal, user=usuario,
                                   text=f"⚠️ No pude leer los tipos de issue disponibles en {JIRA_PROJECT_KEY} "
                                        "(¿el proyecto existe y las credenciales de Jira son correctas?). Intenta de nuevo en un momento.")
        return

    logger.info("/crear-ticket abierto por usuario=%s en canal=%s (tipos=%s)", usuario, canal, tipos)
    client.views_open(trigger_id=command["trigger_id"], view=_modal_crear_ticket(tipos))


@app.view("modal_crear_ticket")
def on_modal_crear_ticket(ack, body, view, client):
    valores = view["state"]["values"]
    seleccion = valores["b_tipo"]["tipo"].get("selected_option")
    tipo = seleccion["value"] if seleccion else None
    titulo = (valores["b_titulo"]["titulo"].get("value") or "").strip()
    descripcion = (valores["b_descripcion"]["descripcion"].get("value") or "").strip()

    # Validar acá y devolver errores inline en el propio modal (mejor experiencia
    # que aceptar el formulario y recién avisar después que algo estaba mal).
    errores = {}
    if not tipo:
        errores["b_tipo"] = "Selecciona un tipo."
    if not titulo:
        errores["b_titulo"] = "El título no puede estar vacío."
    elif len(titulo) > MAX_TITULO:
        errores["b_titulo"] = f"Máximo {MAX_TITULO} caracteres."
    if len(descripcion) > MAX_DESCRIPCION:
        errores["b_descripcion"] = f"Máximo {MAX_DESCRIPCION} caracteres."
    if errores:
        ack(response_action="errors", errors=errores)
        return
    ack()

    usuario = body["user"]["id"]
    token = _guardar_borrador({
        "proyecto": JIRA_PROJECT_KEY, "tipo": tipo, "titulo": titulo,
        "descripcion": descripcion, "usuario": usuario,
    })
    logger.info("Borrador de ticket %s creado por usuario=%s (pendiente de confirmar)", token, usuario)

    resumen = (
        f"*Vas a crear este ticket:*\n"
        f"• Proyecto: `{JIRA_PROJECT_KEY}`\n"
        f"• Tipo: `{tipo}`\n"
        f"• Título: {titulo}\n"
        f"• Descripción: {descripcion or '_(sin descripción)_'}"
    )
    client.chat_postMessage(
        channel=JIRA_TICKET_CHANNEL_ID,
        text=resumen,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": resumen}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Confirmar"}, "style": "primary",
                 "action_id": "crear_ticket_confirmar", "value": token},
                {"type": "button", "text": {"type": "plain_text", "text": "Cancelar"},
                 "action_id": "crear_ticket_cancelar", "value": token},
            ]},
        ],
    )


@app.action("crear_ticket_confirmar")
def on_confirmar_ticket(ack, body, client):
    ack()
    token = body["actions"][0]["value"]
    canal = body["channel"]["id"]
    ts = body["message"]["ts"]
    usuario_click = body["user"]["id"]
    datos = _borradores.pop(token, None)

    if not datos:
        client.chat_update(channel=canal, ts=ts, text="⚠️ Esta confirmación ya expiró o ya se usó.")
        return
    if usuario_click != datos["usuario"]:
        # Evita que un tercero confirme/cancele una creación que no inició.
        client.chat_postEphemeral(channel=canal, user=usuario_click,
                                   text="Solo quien inició la creación puede confirmarla.")
        _borradores[token] = datos  # lo devolvemos: el intento invalido no debe consumirlo
        return

    resultado = crear_ticket(datos["proyecto"], datos["tipo"], datos["titulo"], datos["descripcion"])
    if "error" in resultado:
        logger.warning("crear_ticket fallo para usuario=%s: %s", datos["usuario"], resultado["error"])
        client.chat_update(channel=canal, ts=ts, text=f"❌ No se pudo crear el ticket: {resultado['error']}")
        return

    ticket, url = resultado["ticket"], resultado["url"]
    logger.info("Ticket %s creado por usuario=%s desde canal=%s", ticket, datos["usuario"], canal)

    nota = f"Creado vía DeployGo Assistant por <@{datos['usuario']}> en <#{canal}>."
    comentario = comentar_ticket(ticket, nota)
    if "error" in comentario:
        logger.warning("No se pudo dejar el comentario de trazabilidad en %s: %s", ticket, comentario["error"])

    client.chat_update(
        channel=canal, ts=ts,
        text=f"✅ Ticket creado: {url}",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"✅ *Ticket creado:* <{url}|{ticket}> — {datos['titulo']}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "📄 Armar spec"},
                 "action_id": "armar_spec",
                 "value": json.dumps({"ticket": ticket, "titulo": datos["titulo"]}, ensure_ascii=False)},
            ]},
        ],
    )


@app.action("crear_ticket_cancelar")
def on_cancelar_ticket(ack, body, client):
    ack()
    token = body["actions"][0]["value"]
    _borradores.pop(token, None)
    client.chat_update(channel=body["channel"]["id"], ts=body["message"]["ts"],
                        text="Cancelado. No se creó ningún ticket.")


@app.action("armar_spec")
def on_armar_spec(ack, body, client):
    ack()
    datos = json.loads(body["actions"][0]["value"])
    ticket = datos["ticket"]
    borrador = _borrador_spec(ticket, datos["titulo"])
    texto = (
        f"Borrador de `specs/{ticket}.yml` -- ajusta los TODO antes de "
        f"commitearlo como primer commit de la rama:\n```{borrador}```"
    )
    client.chat_postMessage(channel=body["channel"]["id"], thread_ts=body["message"]["ts"], text=texto)


if __name__ == "__main__":
    faltan = [v for v in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ANTHROPIC_API_KEY",
                          "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "GITHUB_REPO")
              if not os.environ.get(v)]
    if faltan:
        raise SystemExit(f"❌ Faltan variables de entorno: {', '.join(faltan)}")
    print("🚀 DeployGo Assistant conectado a Slack (Socket Mode). Ctrl+C para detener.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
