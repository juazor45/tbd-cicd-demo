#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
policy.py — Motor de politicas: Policy Decision Point (PDP).

Carga una politica declarativa desde policies/<nombre>.yml, la evalua contra
un contexto (dict armado por quien llama) y devuelve una Decision con el
resultado de cada regla, para que cada punto de aplicacion (CI, CD, los
bots) le pregunte a este modulo en vez de reimplementar la logica de
matching por su cuenta.

Fase 1: generaliza lo que validate_spec.py ya hacia a mano (matching de
archivos contra cambios_permitidos/cambios_prohibidos declarados en
specs/<TICKET>.yml). Las fases siguientes (deploy-policy, bot-policy) van
a reusar el mismo evaluador sin tocar este archivo, solo agregando
predicados nuevos al registro PREDICADOS.

Sin dependencias externas, misma filosofia que el resto de scripts/: el
parser de policies/*.yml es minimo, a medida de la forma fija que usan
estos archivos (mapas anidados, listas de escalares, listas de mapas) --
no es un parser YAML general, no reemplaza a PyYAML.
"""

import fnmatch
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

POLICIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "policies")


# ----------------------------------------------------------------------
# Parser minimo de policies/*.yml
# ----------------------------------------------------------------------
def _limpiar_lineas(texto):
    lineas = []
    for raw in texto.splitlines():
        sin_nl = raw.rstrip("\n")
        if not sin_nl.strip() or sin_nl.strip().startswith("#"):
            continue
        lineas.append(sin_nl)
    return lineas


def _indent(linea):
    return len(linea) - len(linea.lstrip(" "))


def _escalar(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s


class _Cursor:
    """Envuelve la lista de lineas + una posicion mutable, para que las
    funciones de parseo recursivas avancen sobre el mismo estado."""

    def __init__(self, lineas):
        self.lineas = lineas
        self.pos = 0

    def linea_actual(self):
        return self.lineas[self.pos] if self.pos < len(self.lineas) else None


def _parsear_mapa(cur, nivel):
    resultado = {}
    while True:
        linea = cur.linea_actual()
        if linea is None or _indent(linea) < nivel:
            break
        contenido = linea.strip()
        if contenido.startswith("- "):
            break  # este nivel en realidad es una lista, no un mapa
        clave, _, resto = contenido.partition(":")
        clave = clave.strip()
        resto = resto.strip()
        cur.pos += 1
        siguiente = cur.linea_actual()
        if resto:
            resultado[clave] = _escalar(resto)
        elif siguiente is not None and _indent(siguiente) > nivel:
            if siguiente.strip().startswith("- "):
                resultado[clave] = _parsear_lista(cur, _indent(siguiente))
            else:
                resultado[clave] = _parsear_mapa(cur, _indent(siguiente))
        else:
            resultado[clave] = None
    return resultado


def _parsear_lista(cur, nivel):
    items = []
    while True:
        linea = cur.linea_actual()
        if linea is None or _indent(linea) < nivel or not linea.strip().startswith("- "):
            break
        ind_guion = _indent(linea)
        resto = linea.strip()[2:]
        cur.pos += 1
        if ":" in resto:
            # item de mapa: "- clave: valor" (+ posibles claves hermanas
            # indentadas 2 espacios mas que el guion)
            item = {}
            clave, _, valor = resto.partition(":")
            clave = clave.strip()
            valor = valor.strip()
            siguiente = cur.linea_actual()
            if valor:
                item[clave] = _escalar(valor)
            elif siguiente is not None and _indent(siguiente) > ind_guion:
                if siguiente.strip().startswith("- "):
                    item[clave] = _parsear_lista(cur, _indent(siguiente))
                else:
                    item[clave] = _parsear_mapa(cur, _indent(siguiente))
            sub_nivel = ind_guion + 2
            while True:
                sub = cur.linea_actual()
                if sub is None or _indent(sub) != sub_nivel or sub.strip().startswith("- "):
                    break
                sub_clave, _, sub_resto = sub.strip().partition(":")
                sub_clave = sub_clave.strip()
                sub_resto = sub_resto.strip()
                cur.pos += 1
                sub_siguiente = cur.linea_actual()
                if sub_resto:
                    item[sub_clave] = _escalar(sub_resto)
                elif sub_siguiente is not None and _indent(sub_siguiente) > sub_nivel:
                    if sub_siguiente.strip().startswith("- "):
                        item[sub_clave] = _parsear_lista(cur, _indent(sub_siguiente))
                    else:
                        item[sub_clave] = _parsear_mapa(cur, _indent(sub_siguiente))
                else:
                    item[sub_clave] = None
            items.append(item)
        else:
            items.append(_escalar(resto))
    return items


def cargar_yaml_simple(path):
    with open(path, encoding="utf-8") as f:
        texto = f.read()
    cur = _Cursor(_limpiar_lineas(texto))
    return _parsear_mapa(cur, 0)


# ----------------------------------------------------------------------
# Resolucion de templates "{{ ruta.en.el.contexto }}"
# ----------------------------------------------------------------------
def _resolver(valor, contexto):
    """Si valor es un string "{{ a.b.c }}", devuelve contexto['a']['b']['c'].
    Si no es un template, lo devuelve tal cual (permite valores literales
    ademas de referencias al contexto)."""
    if not isinstance(valor, str):
        return valor
    s = valor.strip()
    if not (s.startswith("{{") and s.endswith("}}")):
        return valor
    ruta = s[2:-2].strip()
    actual = contexto
    for parte in ruta.split("."):
        if isinstance(actual, dict):
            actual = actual.get(parte)
        else:
            return None
    return actual


# ----------------------------------------------------------------------
# Predicados: cada clave de "require" mapea a una funcion
# (valor_esperado_resuelto, contexto) -> (ok: bool, detalle: list[str])
# ----------------------------------------------------------------------
def _pred_archivos_dentro_de(patrones, contexto):
    """Falla por cada archivo que NO matchea ninguno de 'patrones'."""
    patrones = patrones or []
    archivos = contexto.get("archivos_modificados", [])
    excluir = set(contexto.get("excluir", []))
    fallas = []
    if not patrones:
        return True, fallas
    for path in archivos:
        if path in excluir:
            continue
        if not any(fnmatch.fnmatch(path, p) for p in patrones):
            fallas.append({"archivo": path})
    return (len(fallas) == 0), fallas


def _pred_archivos_fuera_de(patrones, contexto):
    """Falla por cada archivo que matchea alguno de 'patrones' (prohibidos).
    Guarda tambien que patron especifico matcheo, para poder mostrarlo."""
    patrones = patrones or []
    archivos = contexto.get("archivos_modificados", [])
    excluir = set(contexto.get("excluir", []))
    fallas = []
    for path in archivos:
        if path in excluir:
            continue
        for patron in patrones:
            if fnmatch.fnmatch(path, patron):
                fallas.append({"archivo": path, "patron": patron})
                break
    return (len(fallas) == 0), fallas


def _pred_rama_es(esperada, contexto):
    rama = contexto.get("rama")
    ok = rama == esperada
    detalle = [] if ok else [{"rama": rama, "esperada": esperada}]
    return ok, detalle


def _pred_jira_estado_en(estados_validos, contexto):
    estados_validos = estados_validos or []
    estado = contexto.get("jira_estado")
    ok = estado in estados_validos
    detalle = [] if ok else [{"jira_estado": estado, "esperados": estados_validos}]
    return ok, detalle


PREDICADOS = {
    "archivos_dentro_de": _pred_archivos_dentro_de,
    "archivos_fuera_de": _pred_archivos_fuera_de,
    "rama_es": _pred_rama_es,
    "jira_estado_en": _pred_jira_estado_en,
}


# ----------------------------------------------------------------------
# Decision
# ----------------------------------------------------------------------
class Decision:
    def __init__(self, politica, enforcement):
        self.politica = politica
        self.enforcement = enforcement
        self.violaciones = []  # [{"regla_id": ..., "mensaje": ..., "detalle": [...]}]

    @property
    def permitido(self):
        return len(self.violaciones) == 0

    def __repr__(self):
        estado = "OK" if self.permitido else f"{len(self.violaciones)} violacion(es)"
        return f"<Decision politica={self.politica} enforcement={self.enforcement} {estado}>"


def evaluar(nombre_politica, contexto):
    """Carga policies/<nombre_politica>.yml y evalua cada regla contra
    contexto. Nunca lanza excepcion por una regla individual mal formada --
    la registra como violacion con detalle, para que un typo en la politica
    sea visible en vez de tumbar silenciosamente el pipeline."""
    path = os.path.join(POLICIES_DIR, f"{nombre_politica}.yml")
    politica = cargar_yaml_simple(path)
    enforcement = politica.get("enforcement", "advisory")
    decision = Decision(nombre_politica, enforcement)

    for regla in politica.get("rules", []):
        regla_id = regla.get("id", "(sin id)")
        require = regla.get("require", {}) or {}
        mensaje = (regla.get("on_fail", {}) or {}).get("mensaje", "regla incumplida")

        for clave, valor_crudo in require.items():
            predicado = PREDICADOS.get(clave)
            if predicado is None:
                decision.violaciones.append({
                    "regla_id": regla_id,
                    "mensaje": f"predicado desconocido: '{clave}'",
                    "detalle": [],
                })
                logger.warning("policy(%s): predicado desconocido '%s' en regla '%s'", nombre_politica, clave, regla_id)
                continue
            valor = _resolver(valor_crudo, contexto)
            ok, detalle = predicado(valor, contexto)
            if not ok:
                decision.violaciones.append({
                    "regla_id": regla_id,
                    "mensaje": mensaje,
                    "detalle": detalle,
                })

    logger.info("policy(%s): %s", nombre_politica, decision)
    return decision


# ----------------------------------------------------------------------
# CLI: para llamarlo desde un workflow en bash (cicd-cert.yml, etc.), sin
# tener que escribir un wrapper en Python como hace validate_spec.py para
# spec-compliance. Uso:
#   python3 scripts/policy.py --politica deploy-policy --contexto '{"rama": "main", "jira_estado": "Construcción Done"}'
# Imprime un reporte legible y termina con exit 0 si esta permitido (o si
# la politica es advisory), exit 1 si esta bloqueado y enforcement=blocking
# -- pensado para un step de GitHub Actions que corre con 'bash -e' por
# defecto: un exit 1 aca aborta el step sin necesitar chequear $? a mano.
# ----------------------------------------------------------------------
def _formatear_mensaje(mensaje, contexto):
    try:
        return mensaje.format(**contexto)
    except (KeyError, IndexError):
        return mensaje


def _main_cli():
    import argparse

    parser = argparse.ArgumentParser(description="Evalua una politica declarativa de policies/*.yml contra un contexto.")
    parser.add_argument("--politica", required=True, help="Nombre del archivo en policies/ (sin .yml)")
    parser.add_argument("--contexto", required=True, help="Contexto en JSON, ej: '{\"rama\": \"main\"}'")
    args = parser.parse_args()

    try:
        contexto = json.loads(args.contexto)
    except json.JSONDecodeError as e:
        print(f"❌ --contexto no es JSON valido: {e}")
        sys.exit(1)

    decision = evaluar(args.politica, contexto)

    if decision.permitido:
        print(f"✅ {args.politica}: OK")
        sys.exit(0)

    for v in decision.violaciones:
        print(f"❌ [{v['regla_id']}] {_formatear_mensaje(v['mensaje'], contexto)}")

    etiqueta = "BLOQUEANTE" if decision.enforcement == "blocking" else "ADVISORIO (no bloquea)"
    print(f"\n{etiqueta}: {args.politica} no se cumple")

    if decision.enforcement == "blocking":
        sys.exit(1)


if __name__ == "__main__":
    _main_cli()
