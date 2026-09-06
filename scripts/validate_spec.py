#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_spec.py — Valida que un PR se mantenga dentro de los limites
declarados en specs/<TICKET>.yml (cambios_permitidos / cambios_prohibidos).

Desde la Fase 1 de policy as code, este script ya NO decide por su cuenta
que es una violacion: arma el contexto (que dice el spec del ticket, que
archivos toco el PR) y se lo pasa a policy.evaluar("spec-compliance", ...),
en policy.py. La regla en si vive en policies/spec-compliance.yml -- este
archivo solo la alimenta con los datos de este ticket puntual y traduce la
Decision resultante al mismo formato de mensajes que ya se mostraba antes
del refactor, para no romper la lectura del step en ci-pr.yml.

MODO ADVISORIO: el enforcement real lo declara policies/spec-compliance.yml
(hoy "advisory": informa, no falla el build). Cuando el equipo tenga el
habito de crear el spec al iniciar cada ticket, alcanza con cambiar ese
campo a "blocking" en el YAML -- no hace falta tocar este script.

Uso (pensado para correr dentro de ci-pr.yml):
  python3 scripts/validate_spec.py <rama> <archivo_con_lista_de_paths>

<archivo_con_lista_de_paths> es un archivo de texto con la salida de
`git diff --name-only` contra la rama base (una ruta por linea).

Sin dependencias externas: parser minimo de YAML (misma filosofia que
release-status.py), solo entiende la estructura fija de specs/TEMPLATE.yml.
No usa PyYAML a proposito, para no agregar un paso de "pip install" a un
workflow que hoy no lo necesita.
"""

import logging
import os
import re
import sys

import policy

SPECS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "specs")
LIST_KEYS = ("cambios_permitidos", "cambios_prohibidos", "evidencia_requerida")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def extraer_ticket(rama):
    # Sin re.IGNORECASE a proposito: la convencion de ramas exige el ticket
    # en mayusculas (ver jira-branch.yml, que usa el mismo criterio en bash).
    # Con IGNORECASE, ramas como 'dependabot/.../checkout-7.0.1' matchean
    # falsamente como ticket "CHECKOUT-7".
    m = re.search(r"[A-Z][A-Z0-9]+-[0-9]+", rama or "")
    return m.group(0) if m else None


def parsear_spec(path):
    """Parser minimo: separa escalares ('key: value'), listas ('key:' + '  - item')
    y bloques de texto estilo YAML folded/literal ('key: >' o 'key: |' + lineas
    indentadas). No es un parser YAML completo -- solo entiende la estructura
    fija de specs/TEMPLATE.yml. Es deliberadamente independiente del parser de
    policy.py: ese esta hecho a medida de policies/*.yml (mapas/listas
    anidados, sin bloques folded), y specs/*.yml si usa bloques folded para
    'contrato' -- son dos formas de YAML distintas, cada una con su parser
    minimo en vez de forzar un parser generico a cubrir ambas."""
    datos = {}
    clave_actual = None
    en_bloque = False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            linea = raw.rstrip("\n")
            if not linea.strip() or linea.strip().startswith("#"):
                continue
            if linea.startswith((" ", "\t")):
                if en_bloque:
                    previo = datos.get(clave_actual, "")
                    datos[clave_actual] = (previo + " " + linea.strip()).strip()
                    continue
                if clave_actual in LIST_KEYS and linea.strip().startswith("-"):
                    item = linea.strip()[1:].strip().strip('"').strip("'")
                    datos.setdefault(clave_actual, []).append(item)
                continue
            en_bloque = False
            if ":" in linea:
                clave, _, resto = linea.partition(":")
                clave_actual = clave.strip()
                resto = resto.strip()
                if resto in (">", "|"):
                    en_bloque = True
                    datos[clave_actual] = ""
                elif resto:
                    datos[clave_actual] = resto.strip('"').strip("'")
    return datos


def _formatear_violacion(v):
    """Traduce una violacion de la Decision al mismo texto que mostraba la
    version anterior (pre-Fase 1) de este script."""
    detalle = v["detalle"]
    lineas = []
    for item in detalle:
        archivo = item.get("archivo", "?")
        if "patron" in item:
            lineas.append(f"❌ '{archivo}' coincide con un patron PROHIBIDO ('{item['patron']}')")
        else:
            lineas.append(f"⚠️ '{archivo}' {v['mensaje']}")
    return lineas


def main():
    if len(sys.argv) < 3:
        print("Uso: validate_spec.py <rama> <archivo_con_lista_de_paths>")
        sys.exit(0)

    rama, archivo_diff = sys.argv[1], sys.argv[2]
    ticket = extraer_ticket(rama)
    logger.info("validate_spec: rama=%s ticket_detectado=%s", rama, ticket)

    if not ticket:
        print(f"ℹ️ La rama '{rama}' no tiene ticket detectable; se omite la validacion de spec.")
        return

    spec_path = os.path.join(SPECS_DIR, f"{ticket}.yml")
    if not os.path.isfile(spec_path):
        print(f"⚠️ No existe specs/{ticket}.yml — este PR no tiene un spec declarado.")
        logger.warning("validate_spec: no existe %s", spec_path)
        return

    spec = parsear_spec(spec_path)

    with open(archivo_diff, encoding="utf-8") as f:
        archivos = [l.strip() for l in f if l.strip()]

    print(f"📋 Spec: specs/{ticket}.yml — {spec.get('titulo', '(sin titulo)')}")
    print(f"   Archivos en el PR: {len(archivos)}")

    ruta_propio_spec = os.path.relpath(spec_path, os.path.join(SPECS_DIR, "..")).replace(os.sep, "/")

    contexto = {
        "spec": spec,
        "archivos_modificados": archivos,
        "excluir": [ruta_propio_spec],  # todo PR puede crear/actualizar su propio spec
    }
    decision = policy.evaluar("spec-compliance", contexto)

    if not decision.permitido:
        for v in decision.violaciones:
            print("\n".join(_formatear_violacion(v)))
        etiqueta = "ADVISORIO (no bloquea)" if decision.enforcement == "advisory" else "BLOQUEANTE"
        print(f"\n{etiqueta}: el PR se sale de lo declarado en specs/{ticket}.yml")
        logger.warning("validate_spec(%s): %d violacion(es), enforcement=%s", ticket, len(decision.violaciones), decision.enforcement)
        if decision.enforcement == "blocking":
            sys.exit(1)
    else:
        print("✅ El PR se mantiene dentro de los limites declarados en el spec.")
        logger.info("validate_spec(%s): OK, sin hallazgos", ticket)


if __name__ == "__main__":
    main()
