#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
function_app.py — Punto de entrada HTTP para Azure Functions (Python v2).

Expone un único endpoint POST /api/messages: el "messaging endpoint" que se
configura en el recurso de Azure Bot. Bot Framework Service le pega a esta
URL por cada actividad (mensaje, Action.Submit de una Adaptive Card, etc.);
nosotros la deserializamos, la pasamos por el adapter (que valida el token
JWT del request) y la despachamos a DeployGoBot.on_turn.

auth_level=ANONYMOUS es correcto acá: la autenticación real la hace el
adapter validando el header Authorization contra Bot Framework / Entra ID,
no las function keys de Azure Functions.
"""

import json
import logging

import azure.functions as func
from botbuilder.core import TurnContext
from botbuilder.integration.aiohttp import CloudAdapter, ConfigurationBotFrameworkAuthentication
from botbuilder.schema import Activity

from bot import DeployGoBot
from config import DefaultConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG = DefaultConfig()
ADAPTER = CloudAdapter(ConfigurationBotFrameworkAuthentication(CONFIG))
BOT = DeployGoBot()


async def _on_error(turn_context: TurnContext, error: Exception):
    logger.exception("Error no manejado en un turno del bot: %s", error)
    await turn_context.send_activity("⚠️ Ocurrió un error inesperado procesando tu mensaje.")


ADAPTER.on_turn_error = _on_error

app = func.FunctionApp()


@app.route(route="messages", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
async def messages(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(status_code=400, body="Body inválido: se esperaba JSON.")

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(auth_header, activity, BOT.on_turn)

    if response:
        return func.HttpResponse(
            body=json.dumps(response.body) if response.body else None,
            status_code=response.status,
            mimetype="application/json",
        )
    return func.HttpResponse(status_code=201)
