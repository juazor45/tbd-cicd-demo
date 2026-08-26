#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cards.py — Adaptive Cards del flujo /crear-ticket.

Equivalente a los Block Kit blocks del modal y los mensajes de confirmación
en scripts/slack_bot.py, pero en Adaptive Card JSON (schema 1.5). Estas
funciones devuelven el dict crudo de la tarjeta; bot.py lo envuelve con
CardFactory.adaptive_card(...) para adjuntarlo a la Activity.
"""

from tools import MAX_DESCRIPCION, MAX_TITULO


def card_crear_ticket_form(proyecto, tipos):
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "text": "Crear ticket", "weight": "Bolder", "size": "Medium"},
            {
                "type": "TextBlock",
                "text": f"Proyecto: **{proyecto}** (fijo, no editable)",
                "isSubtle": True,
                "wrap": True,
            },
            {
                "type": "Input.ChoiceSet",
                "id": "tipo",
                "label": "Tipo",
                "isRequired": True,
                "errorMessage": "Selecciona un tipo.",
                "style": "compact",
                "choices": [{"title": t, "value": t} for t in tipos],
            },
            {
                "type": "Input.Text",
                "id": "titulo",
                "label": f"Título (máx. {MAX_TITULO} caracteres)",
                "isRequired": True,
                "errorMessage": "El título no puede estar vacío.",
                "maxLength": MAX_TITULO,
            },
            {
                "type": "Input.Text",
                "id": "descripcion",
                "label": f"Descripción (opcional, máx. {MAX_DESCRIPCION} caracteres)",
                "isMultiline": True,
                "maxLength": MAX_DESCRIPCION,
            },
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Continuar",
                "data": {"action": "crear_ticket_submit"},
            }
        ],
    }


def card_confirmacion(token, proyecto, tipo, titulo, descripcion):
    desc_texto = descripcion.strip() if descripcion and descripcion.strip() else "_(sin descripción)_"
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "text": "Confirmar creación de ticket", "weight": "Bolder", "size": "Medium"},
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Proyecto", "value": proyecto},
                    {"title": "Tipo", "value": tipo},
                    {"title": "Título", "value": titulo},
                ],
            },
            {"type": "TextBlock", "text": "Descripción:", "weight": "Bolder"},
            {"type": "TextBlock", "text": desc_texto, "wrap": True},
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "✅ Confirmar",
                "style": "positive",
                "data": {"action": "crear_ticket_confirmar", "token": token},
            },
            {
                "type": "Action.Submit",
                "title": "Cancelar",
                "data": {"action": "crear_ticket_cancelar", "token": token},
            },
        ],
    }


def card_resultado_creado(ticket, url, titulo):
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": f"✅ Ticket **{ticket}** creado.",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": f"[{url}]({url})",
                "wrap": True,
            },
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "📄 Armar spec",
                "data": {"action": "armar_spec", "ticket": ticket, "titulo": titulo},
            }
        ],
    }
