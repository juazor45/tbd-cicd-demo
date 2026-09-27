#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_log.py — Log de decisiones de agentes (Fase 2 del SDLC agentico).

Envuelve una tool de tools.py para emitir, en cada invocacion, un registro
con quien la llamo (agente_id, resuelto contra agents/*.yml), que tool fue,
con que entrada, que devolvio (resumido) y cuanto tardo. No cambia la firma
ni el valor de retorno de la funcion envuelta -- es un decorator transparente,
pensado para colgarse de TOOL_FUNCTIONS sin tocar la logica de cada tool.

Dos salidas, sin sumar infraestructura nueva:
  - logging.getLogger("agent_log"), nivel INFO: una linea JSON por invocacion,
    para que cada interfaz (Slack bot, Teams Function, Actions, CLI) la vea
    en su propio stdout/log de siempre, tal como ya loguea cada tool hoy.
  - un archivo JSONL local (AGENT_LOG_FILE, default agent-log.jsonl junto a
    este script) -- best-effort: si el proceso no tiene disco persistente o
    se reinicia (Azure Functions Consumption), se pierde, igual que ya pierde
    el resto del estado en memoria de los bots (ver README, "Estado en
    memoria"). Sirve para correr este mismo script como CLI y ver un resumen
    de lo que paso en ESE proceso.

Nota deliberada de alcance: canal y usuario no viajan hasta aca todavia --
las funciones de tools.py no reciben ese contexto hoy (lo tiene el bot que
llama, no la tool). Threadear eso es una iteracion siguiente; por ahora el
log identifica CUAL agente y CUAL tool, no QUIEN lo disparo. Centralizar
estos logs entre procesos (Slack en Codespaces, Teams en Azure Functions,
Actions) requeriria un almacen compartido (Azure Table Storage, que el
README ya preve para el estado de los bots) -- eso es Azure, y a proposito
no se toca en esta fase.
"""

import functools
import json
import logging
import os
import time

import agent_registry

logger = logging.getLogger("agent_log")

_AGENT_LOG_FILE_OVERRIDE = os.environ.get("AGENT_LOG_FILE")
AGENT_LOG_FILE = (
    _AGENT_LOG_FILE_OVERRIDE
    if _AGENT_LOG_FILE_OVERRIDE
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-log.jsonl")
)

_TOOL_A_AGENTE = {}


def _agente_de_tool(nombre_tool):
    """Resuelve a que agente pertenece una tool, leyendo agents/*.yml (con
    cache en memoria). 'desconocido' si ninguna manifest la declara -- una
    tool sin agente asignado todavia no debe romper el logging."""
    if not _TOOL_A_AGENTE:
        for agente_id in agent_registry.agentes_declarados():
            for nombre in agent_registry.tools_de(agente_id):
                _TOOL_A_AGENTE.setdefault(nombre, agente_id)
    return _TOOL_A_AGENTE.get(nombre_tool, "desconocido")


def _resumir(valor, max_chars=300):
    """Version corta de un resultado para el log -- nunca vuelca specs o
    templates completos (pueden ser largos) a una linea de log."""
    texto = json.dumps(valor, ensure_ascii=False, default=str)
    if len(texto) > max_chars:
        return texto[:max_chars] + f"...(+{len(texto) - max_chars} chars)"
    return texto


def instrumentar(nombre_tool, funcion):
    """Decorator: envuelve 'funcion' (una tool de tools.py) para loguear
    cada invocacion. Preserva firma y valor de retorno -- transparente para
    quien la llama (TOOL_FUNCTIONS.get(nombre)(**args) sigue funcionando
    exactamente igual)."""

    @functools.wraps(funcion)
    def envoltorio(*args, **kwargs):
        inicio = time.time()
        agente_id = _agente_de_tool(nombre_tool)
        entrada = {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}
        try:
            resultado = funcion(*args, **kwargs)
            es_error = isinstance(resultado, dict) and "error" in resultado
            _emitir({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "agente_id": agente_id,
                "tool": nombre_tool,
                "entrada": entrada,
                "resultado": _resumir(resultado),
                "error": es_error,
                "duracion_ms": round((time.time() - inicio) * 1000, 1),
            })
            return resultado
        except Exception as e:
            _emitir({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "agente_id": agente_id,
                "tool": nombre_tool,
                "entrada": entrada,
                "resultado": None,
                "error": True,
                "excepcion": f"{type(e).__name__}: {e}",
                "duracion_ms": round((time.time() - inicio) * 1000, 1),
            })
            raise

    return envoltorio


def _emitir(registro):
    linea = json.dumps(registro, ensure_ascii=False)
    logger.info(linea)
    try:
        with open(AGENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError as e:
        logger.warning("agent_log: no se pudo escribir %s: %s", AGENT_LOG_FILE, e)


def leer_registros(path=None):
    """Lee agent-log.jsonl (o 'path') y devuelve la lista de registros,
    ignorando lineas corruptas en vez de romper el reporte por una sola."""
    path = path or AGENT_LOG_FILE
    registros = []
    try:
        with open(path, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    registros.append(json.loads(linea))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return registros


def resumen_por_agente(registros=None):
    """{'agente_id': {'llamadas': n, 'errores': n}} -- lo que un reporte (CLI
    o, mas adelante, dashboard.py) necesita para mostrar por agente."""
    registros = registros if registros is not None else leer_registros()
    resumen = {}
    for r in registros:
        agente_id = r.get("agente_id", "desconocido")
        stats = resumen.setdefault(agente_id, {"llamadas": 0, "errores": 0})
        stats["llamadas"] += 1
        if r.get("error"):
            stats["errores"] += 1
    return resumen


if __name__ == "__main__":
    _registros = leer_registros()
    if not _registros:
        print(f"Sin registros todavia en {AGENT_LOG_FILE}")
    else:
        print(f"{len(_registros)} invocaciones registradas en {AGENT_LOG_FILE}\n")
        for _agente_id, _stats in sorted(resumen_por_agente(_registros).items()):
            print(f"  {_agente_id}: {_stats['llamadas']} llamadas, {_stats['errores']} errores")
