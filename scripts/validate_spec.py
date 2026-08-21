#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_spec.py — Valida que un PR se mantenga dentro de los limites
declarados en specs/<TICKET>.yml (cambios_permitidos / cambios_prohibidos).

MODO ADVISORIO: hoy solo informa (no falla el build). Se puede promover a
bloqueante mas adelante cambiando ADVISORY = False, cuando el equipo ya
tenga el habito de crear el spec al iniciar cada ticket.

Uso (pensado para correr dentro de ci-pr.yml):
  python3 scripts/validate_spec.py <rama> <archivo_con_lista_de_paths>

<archivo_con_lista_de_paths> es un archivo de texto con la salida de
`git diff --name-only` contra la rama base (una ruta por linea).

Sin dependencias externas: parser minimo de YAML (misma filosofia que
release-status.py), solo entiende la estructura fija de specs/TEMPLATE.yml.
No usa PyYAML a proposito, para no agregar un paso de "pip install" a un
workflow que hoy no lo necesita.
"""

import fnmatch
import logging
import os
import re
import sys

ADVISORY = True  # cambiar a False para que un hallazgo bloquee el merge

SPECS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "specs")
LIST_KEYS = ("cambios_permitidos", "cambios_prohibidos", "evidencia_requerida")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def extraer_ticket(rama):
    m = re.search(r"[A-Z][A-Z0-9]+-[0-9]+", rama or "", re.IGNORECASE)
    return m.group(0).upper() if m else None


def parsear_spec(path):
    """Parser minimo: separa escalares ('key: value') de listas ('key:' + lineas '  - item')."""
    datos = {}
    clave_actual = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            linea = raw.rstrip("\n")
            if not linea.strip() or linea.strip().startswith("#"):
                continue
            if linea.startswith((" ", "\t")):
                if clave_actual in LIST_KEYS and linea.strip().startswith("-"):
                    item = linea.strip()[1:].strip().strip('"').strip("'")
                    datos.setdefault(clave_actual, []).append(item)
                continue  # otras continuaciones (ej. bloque 'contrato: >') se ignoran aqui
            if ":" in linea:
                clave, _, resto = linea.partition(":")
                clave_actual = clave.strip()
                resto = resto.strip()
                if resto and resto not in (">", "|"):
                    datos[clave_actual] = resto.strip('"').strip("'")
    return datos


def main():
    if len(sys.argv) < 3:
        print("Uso: validate_spec.py <rama> <archivo_con_lista_de_paths>")
        sys.exit(0 if ADVISORY else 1)

    rama, archivo_diff = sys.argv[1], sys.argv[2]
    ticket = extraer_ticket(rama)
    logger.info("validate_spec: rama=%s ticket_detectado=%s advisory=%s", rama, ticket, ADVISORY)

    if not ticket:
        print(f"ℹ️ La rama '{rama}' no tiene ticket detectable; se omite la validacion de spec.")
        return

    spec_path = os.path.join(SPECS_DIR, f"{ticket}.yml")
    if not os.path.isfile(spec_path):
        print(f"⚠️ No existe specs/{ticket}.yml — este PR no tiene un spec declarado.")
        logger.warning("validate_spec: no existe %s", spec_path)
        if not ADVISORY:
            sys.exit(1)
        return

    spec = parsear_spec(spec_path)
    permitidos = spec.get("cambios_permitidos", [])
    prohibidos = spec.get("cambios_prohibidos", [])

    with open(archivo_diff, encoding="utf-8") as f:
        archivos = [l.strip() for l in f if l.strip()]

    print(f"📋 Spec: specs/{ticket}.yml — {spec.get('titulo', '(sin titulo)')}")
    print(f"   Archivos en el PR: {len(archivos)}")

    hallazgos = []
    for path in archivos:
        for patron in prohibidos:
            if fnmatch.fnmatch(path, patron):
                hallazgos.append(f"❌ '{path}' coincide con un patron PROHIBIDO ('{patron}')")
        if permitidos and not any(fnmatch.fnmatch(path, p) for p in permitidos):
            hallazgos.append(f"⚠️ '{path}' no esta en cambios_permitidos")

    if hallazgos:
        print("\n".join(hallazgos))
        etiqueta = "ADVISORIO (no bloquea)" if ADVISORY else "BLOQUEANTE"
        print(f"\n{etiqueta}: el PR se sale de lo declarado en specs/{ticket}.yml")
        logger.warning("validate_spec(%s): %d hallazgo(s)", ticket, len(hallazgos))
        if not ADVISORY:
            sys.exit(1)
    else:
        print("✅ El PR se mantiene dentro de los limites declarados en el spec.")
        logger.info("validate_spec(%s): OK, sin hallazgos", ticket)


if __name__ == "__main__":
    main()
