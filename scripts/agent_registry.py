#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_registry.py — Registro declarativo de agentes (Agent Registry).

Lee los manifests de agents/*.yml y expone, para cada agente declarado, que
tools tiene permitidas, su nivel de autonomia y su modo de HITL. No mueve ni
reimplementa ninguna tool: tools.py sigue siendo la unica fuente de verdad
de que hace cada tool -- este modulo solo declara, por agente, cuales de
esas tools ya existentes puede invocar. Cero cambios en tools.py, slack_bot.py,
teams_bot/ o assistant.py: este archivo es puramente aditivo.

Reusa el mismo parser minimo de YAML que ya usa policy.py (mapas y listas
anidados por indentacion) -- misma filosofia de cero dependencias externas,
un solo parser para todo scripts/*.yml en vez de sumar PyYAML.

Uso:
    import agent_registry
    agent_registry.tools_de("release-status")      # -> ["consultar_jira", ...]
    agent_registry.manifest_de("ticket-intake")     # -> dict completo del YAML
    agent_registry.agentes_declarados()             # -> ["policy-advisor", ...]

CLI (pensado para correr en ci-pr.yml mas adelante, igual que policy.py):
    python3 scripts/agent_registry.py
"""

import logging
import os

import policy  # reusa cargar_yaml_simple: mismo parser minimo, sin duplicarlo

logger = logging.getLogger(__name__)

_AGENTS_DIR_OVERRIDE = os.environ.get("AGENTS_DIR")
AGENTS_DIR = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), _AGENTS_DIR_OVERRIDE)
    if _AGENTS_DIR_OVERRIDE
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents")
)

CAMPOS_REQUERIDOS = ["id", "owner", "proposito", "tools_permitidas", "nivel_autonomia", "modo_hitl"]

_CACHE = {}


def agentes_declarados():
    """Ids de todos los agentes con manifest en agents/*.yml (sin extension)."""
    try:
        return sorted(n[:-4] for n in os.listdir(AGENTS_DIR) if n.endswith(".yml"))
    except FileNotFoundError:
        return []


def manifest_de(agente_id):
    """Carga (con cache en memoria) el manifest completo de agents/<agente_id>.yml.
    Lanza ValueError si falta algun campo requerido -- un manifest incompleto
    debe romper ruidosamente, no fallar en silencio cuando alguien lo consulte."""
    if agente_id in _CACHE:
        return _CACHE[agente_id]

    path = os.path.join(AGENTS_DIR, f"{agente_id}.yml")
    manifest = policy.cargar_yaml_simple(path)

    faltantes = [c for c in CAMPOS_REQUERIDOS if not manifest.get(c)]
    if faltantes:
        raise ValueError(f"agents/{agente_id}.yml: faltan campos requeridos: {faltantes}")

    tools = manifest.get("tools_permitidas")
    if isinstance(tools, str):
        # un unico valor sin "- " lo deja el parser como string suelto, no lista
        manifest["tools_permitidas"] = [tools]

    logger.info(
        "agent_registry: manifest_de(%s) OK (%d tools permitidas)",
        agente_id, len(manifest["tools_permitidas"]),
    )
    _CACHE[agente_id] = manifest
    return manifest


def tools_de(agente_id):
    """Lista de nombres de tool (strings) que el agente <agente_id> puede
    invocar, segun su manifest. No valida que esos nombres existan de verdad
    en tools.py -- para eso, ver validar_registro()."""
    return manifest_de(agente_id)["tools_permitidas"]


def nivel_autonomia_de(agente_id):
    return manifest_de(agente_id)["nivel_autonomia"]


def validar_registro():
    """Chequeo de consistencia: cada tool listada en cada manifest debe
    existir de verdad en tools.py (TOOL_FUNCTIONS, o las tools de escritura
    que quedan fuera de TOOL_FUNCTIONS a proposito). Pensado para correr en
    CI (ci-pr.yml) apenas cambie algo en agents/*.yml o en tools.py, para
    agarrar un typo o una tool renombrada antes de que llegue a produccion.

    Devuelve una lista de problemas (vacia si todo esta bien) -- no lanza
    excepcion, para que quien llama decida si eso bloquea o solo advierte
    (mismo criterio advisory/blocking que el resto de policy as code)."""
    import tools as tools_mod

    tools_conocidas = set(tools_mod.TOOL_FUNCTIONS) | {
        "crear_ticket", "comentar_ticket", "listar_tipos_issue",
    }

    problemas = []
    for agente_id in agentes_declarados():
        try:
            manifest = manifest_de(agente_id)
        except ValueError as e:
            problemas.append(str(e))
            continue
        for nombre_tool in manifest["tools_permitidas"]:
            if nombre_tool not in tools_conocidas:
                problemas.append(
                    f"agents/{agente_id}.yml declara la tool '{nombre_tool}', "
                    f"que no existe en tools.py"
                )
    return problemas


if __name__ == "__main__":
    _problemas = validar_registro()
    if _problemas:
        for _p in _problemas:
            print(f"❌ {_p}")
        raise SystemExit(1)
    for _agente_id in agentes_declarados():
        _m = manifest_de(_agente_id)
        print(f"✅ {_agente_id}: {_m['nivel_autonomia']} · {_m['modo_hitl']} · tools={_m['tools_permitidas']}")
