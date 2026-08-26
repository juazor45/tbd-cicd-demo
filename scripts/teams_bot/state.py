#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state.py — Estado en memoria del bot de Teams (DeployGo Assistant).

Port 1:1 de las estructuras equivalentes en scripts/slack_bot.py:
  - _hilos       -> historial de conversación por conversationId (LRU, tope MAX_HILOS)
  - _rate_limit  -> ventana deslizante de rate limiting por usuario
  - _borradores  -> borradores de /crear-ticket pendientes de confirmar (FIFO, tope MAX_BORRADORES)
  - _tipos_cache -> cache de tipos de issue de Jira (vive toda la vida del proceso)

Igual que en el bot de Slack, este estado es SOLO EN MEMORIA DEL PROCESO: no hay
persistencia en disco/DB. Eso significa que, si Azure Functions recicla o
escala a cero la instancia (que es justo lo que buscamos para ahorrar costo),
el historial de conversación y los borradores pendientes se pierden -- el
usuario tendría que retomar la pregunta o volver a llenar el formulario. Es el
mismo comportamiento de fondo que ya tenía el bot de Slack (se resetea al
reiniciar el proceso), solo que en Functions puede pasar con más frecuencia.
Si en algún momento esto molesta, la migración a Azure Table Storage es
directa (mismo shape de datos, casi gratis a este volumen).
"""

import time
import uuid
from collections import OrderedDict, deque

# ---- Historial de conversación por hilo ----
MAX_HILOS = 200
MAX_MENSAJES_POR_HILO = 40
_hilos: "OrderedDict[str, list]" = OrderedDict()


def historial(hilo_id):
    if hilo_id in _hilos:
        _hilos.move_to_end(hilo_id)
        return _hilos[hilo_id]
    if len(_hilos) >= MAX_HILOS:
        _hilos.popitem(last=False)
    _hilos[hilo_id] = []
    return _hilos[hilo_id]


# ---- Rate limiting ----
RATE_LIMIT_MAX = 6
RATE_LIMIT_WINDOW_S = 60
MAX_USUARIOS_RATE_LIMIT = 500
_rate_limit: "OrderedDict[str, deque]" = OrderedDict()


def rate_limited(usuario_id):
    if not usuario_id:
        return False
    if usuario_id in _rate_limit:
        _rate_limit.move_to_end(usuario_id)
    else:
        if len(_rate_limit) >= MAX_USUARIOS_RATE_LIMIT:
            _rate_limit.popitem(last=False)
        _rate_limit[usuario_id] = deque()
    marcas = _rate_limit[usuario_id]
    ahora = time.monotonic()
    while marcas and ahora - marcas[0] > RATE_LIMIT_WINDOW_S:
        marcas.popleft()
    if len(marcas) >= RATE_LIMIT_MAX:
        return True
    marcas.append(ahora)
    return False


# ---- Borradores de /crear-ticket ----
MAX_BORRADORES = 200
_borradores: "OrderedDict[str, dict]" = OrderedDict()


def guardar_borrador(datos):
    token = uuid.uuid4().hex[:12]
    if len(_borradores) >= MAX_BORRADORES:
        _borradores.popitem(last=False)
    _borradores[token] = datos
    return token


def tomar_borrador(token):
    """Consume el borrador (pop) -- para confirmar/cancelar de un solo uso."""
    return _borradores.pop(token, None)


def devolver_borrador(token, datos):
    """Reinsertar un borrador tras un intento de confirmación inválido
    (ej. click de alguien que no inició el flujo) -- ese intento no debe
    consumir el token."""
    _borradores[token] = datos


# ---- Cache de tipos de issue de Jira ----
_tipos_cache = None


def get_tipos_cache():
    return _tipos_cache


def set_tipos_cache(tipos):
    global _tipos_cache
    _tipos_cache = tipos
