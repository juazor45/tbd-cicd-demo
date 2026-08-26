#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py — DeployGo Assistant para Microsoft Teams.

Port 1:1 de la lógica de negocio de scripts/slack_bot.py a Bot Framework SDK.
Mismos controles de seguridad, mismos flujos, mismas herramientas -- lo único
que cambia es la capa de UI: Block Kit (modal + botones) se reemplaza por
Adaptive Cards embebidas en la conversación (ver cards.py), y Socket Mode se
reemplaza por el webhook HTTP que atiende function_app.py.

Simplificación consciente respecto a Slack: el bot de Slack iba actualizando
el mensaje placeholder con las últimas tools invocadas mientras el agente
pensaba (progreso cosmético). Acá se simplificó a un único placeholder que se
reemplaza por la respuesta final -- portar el progreso incremental requeriría
mezclar el loop síncrono del agente con las llamadas async de Bot Framework,
y no vale la complejidad para un efecto puramente cosmético.
"""

import json
import logging
import os
import re

from botbuilder.core import ActivityHandler, CardFactory, MessageFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes

from agent import consultar_agente
from cards import card_confirmacion, card_crear_ticket_form, card_resultado_creado
from state import (
    devolver_borrador,
    get_tipos_cache,
    guardar_borrador,
    rate_limited,
    set_tipos_cache,
    tomar_borrador,
)
from tools import comentar_ticket, crear_ticket, listar_tipos_issue

logger = logging.getLogger(__name__)

JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "")
TEAMS_TICKET_CHANNEL_ID = os.environ.get("TEAMS_TICKET_CHANNEL_ID", "")

TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+", re.IGNORECASE)
CREAR_TICKET_RE = re.compile(r"^/?crear-ticket\b", re.IGNORECASE)


def _channel_id(turn_context: TurnContext) -> str:
    """ID del canal de Teams. En conversaciones de canal viene en channelData;
    en chats 1:1 no aplica (se usa conversation.id como fallback, que nunca va
    a matchear TEAMS_TICKET_CHANNEL_ID -- que es la conducta correcta: igual
    que en Slack, /crear-ticket solo funciona en el canal designado)."""
    channel_data = turn_context.activity.channel_data or {}
    canal = (channel_data.get("channel") or {}).get("id")
    return canal or (turn_context.activity.conversation.id if turn_context.activity.conversation else "")


def _tipos_disponibles():
    cache = get_tipos_cache()
    if cache:
        return cache
    resultado = listar_tipos_issue(JIRA_PROJECT_KEY)
    tipos = resultado.get("tipos") if isinstance(resultado, dict) else None
    if not tipos:
        return None
    set_tipos_cache(tipos)
    return tipos


def _borrador_spec(ticket, titulo):
    titulo_esc = (titulo or "").replace('"', "'")
    return f"""ticket: {ticket}
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
"""


class DeployGoBot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        activity = turn_context.activity
        usuario = activity.from_property.id if activity.from_property else None

        # Action.Submit de una Adaptive Card llega con `value` poblado (no texto libre)
        if activity.value:
            await self._manejar_card_action(turn_context, activity.value, usuario)
            return

        texto = TurnContext.remove_recipient_mention(activity) or ""
        texto = texto.strip()
        if not texto:
            await turn_context.send_activity("Decime en qué te puedo ayudar (o escribí `/crear-ticket`).")
            return

        if CREAR_TICKET_RE.match(texto):
            await self._iniciar_crear_ticket(turn_context, usuario)
            return

        # Cualquier otro texto: pregunta libre al agente (equivalente a
        # on_mention/on_dm/cmd_release en Slack -- Teams no distingue slash
        # commands nativos sin registrar comandos en el manifest, así que
        # todo lo demás se trata como pregunta conversacional).
        await self._responder(turn_context, texto, usuario)

    # ---------------- /crear-ticket ----------------

    async def _iniciar_crear_ticket(self, turn_context: TurnContext, usuario):
        if not TEAMS_TICKET_CHANNEL_ID or not JIRA_PROJECT_KEY:
            await turn_context.send_activity(
                "⚠️ Este comando no está configurado todavía (faltan TEAMS_TICKET_CHANNEL_ID / JIRA_PROJECT_KEY)."
            )
            return
        canal_actual = _channel_id(turn_context)
        if canal_actual != TEAMS_TICKET_CHANNEL_ID:
            await turn_context.send_activity(
                "⚠️ `/crear-ticket` solo funciona en el canal/chat designado para esto.\n\n"
                f"El ID de esta conversación es:\n`{canal_actual}`\n\n"
                "Si querés habilitarla acá, actualizá la variable `TEAMS_TICKET_CHANNEL_ID` en GitHub con ese valor."
            )
            return
        if rate_limited(usuario):
            await turn_context.send_activity("⏳ Estás usando el comando muy seguido. Espera un minuto.")
            return
        tipos = _tipos_disponibles()
        if not tipos:
            await turn_context.send_activity(
                "⚠️ No pude obtener los tipos de issue de Jira (revisá el proyecto o las credenciales)."
            )
            return
        card = CardFactory.adaptive_card(card_crear_ticket_form(JIRA_PROJECT_KEY, tipos))
        await turn_context.send_activity(MessageFactory.attachment(card))

    async def _manejar_card_action(self, turn_context: TurnContext, value: dict, usuario):
        accion = value.get("action")

        if accion == "crear_ticket_submit":
            tipo = (value.get("tipo") or "").strip()
            titulo = (value.get("titulo") or "").strip()
            descripcion = (value.get("descripcion") or "").strip()
            errores = []
            if not tipo:
                errores.append("Selecciona un tipo.")
            if not titulo:
                errores.append("El título no puede estar vacío.")
            elif len(titulo) > 120:
                errores.append("El título supera el máximo de 120 caracteres.")
            if len(descripcion) > 2000:
                errores.append("La descripción supera el máximo de 2000 caracteres.")
            if errores:
                await turn_context.send_activity(
                    "⚠️ No se pudo continuar:\n- " + "\n- ".join(errores) + "\n\nEscribí `/crear-ticket` de nuevo para reintentar."
                )
                return
            token = guardar_borrador(
                {
                    "proyecto": JIRA_PROJECT_KEY,
                    "tipo": tipo,
                    "titulo": titulo,
                    "descripcion": descripcion,
                    "usuario": usuario,
                }
            )
            card = CardFactory.adaptive_card(card_confirmacion(token, JIRA_PROJECT_KEY, tipo, titulo, descripcion))
            await turn_context.send_activity(MessageFactory.attachment(card))
            return

        if accion == "crear_ticket_confirmar":
            token = value.get("token")
            datos = tomar_borrador(token)
            if not datos:
                await turn_context.send_activity("⚠️ Esta confirmación ya expiró o ya se usó.")
                return
            if usuario != datos["usuario"]:
                devolver_borrador(token, datos)
                await turn_context.send_activity("Solo quien inició la creación puede confirmarla.")
                return
            resultado = crear_ticket(datos["proyecto"], datos["tipo"], datos["titulo"], datos["descripcion"])
            if "error" in resultado:
                logger.warning("Error creando ticket: %s", resultado["error"])
                await turn_context.send_activity(f"❌ No se pudo crear el ticket: {resultado['error']}")
                return
            ticket, url = resultado["ticket"], resultado["url"]
            logger.info("Ticket %s creado por %s", ticket, usuario)
            canal = _channel_id(turn_context)
            nota = f"Creado vía DeployGo Assistant (Teams) por {usuario} en el canal {canal}."
            comentario = comentar_ticket(ticket, nota)
            if "error" in comentario:
                logger.warning("No se pudo comentar el ticket %s: %s", ticket, comentario["error"])
            card = CardFactory.adaptive_card(card_resultado_creado(ticket, url, datos["titulo"]))
            await turn_context.send_activity(MessageFactory.attachment(card))
            return

        if accion == "crear_ticket_cancelar":
            token = value.get("token")
            tomar_borrador(token)
            await turn_context.send_activity("Cancelado. No se creó ningún ticket.")
            return

        if accion == "armar_spec":
            ticket = value.get("ticket")
            titulo = value.get("titulo")
            borrador = _borrador_spec(ticket, titulo)
            await turn_context.send_activity(
                f"Borrador de `specs/{ticket}.yml` -- ajustá los `TODO` antes de commitearlo como primer commit de la rama:\n\n```yaml\n{borrador}\n```"
            )
            return

        logger.warning("Acción de card desconocida: %s", accion)

    # ---------------- Chat conversacional ----------------

    async def _responder(self, turn_context: TurnContext, pregunta, usuario):
        if rate_limited(usuario):
            await turn_context.send_activity("⏳ Estás consultando muy seguido. Espera un minuto e intenta de nuevo.")
            return

        placeholder = await turn_context.send_activity("_Consultando Jira y GitHub…_")
        hilo_id = turn_context.activity.conversation.id

        try:
            texto = consultar_agente(pregunta, hilo_id)
        except Exception:
            logger.exception("Fallo consultando al agente")
            texto = "⚠️ No pude completar la consulta. Intenta de nuevo en unos segundos."

        try:
            update = Activity(
                id=placeholder.id,
                type=ActivityTypes.message,
                text=texto,
            )
            await turn_context.update_activity(update)
        except Exception:
            # Igual que en Slack: el "reemplazo del placeholder" es la
            # entrega real de la respuesta -- si update_activity falla (por
            # ejemplo, el canal no lo soporta), mandamos la respuesta como
            # mensaje nuevo en vez de perderla.
            logger.exception("No se pudo actualizar el placeholder, mando la respuesta como mensaje nuevo")
            await turn_context.send_activity(texto)
