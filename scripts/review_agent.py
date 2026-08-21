#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_agent.py — Agente de review automatizado contra el spec del ticket.

Lee el diff real de un PR y el spec declarado (specs/<TICKET>.yml), le pide
a Claude que evalue si el cambio respeta el contrato y los limites
declarados, y si hay evidencia de lo que el spec pide, y publica (o
actualiza) un comentario en el PR con el resultado.

Complementa a validate_spec.py: ese solo compara RUTAS de archivo; este
entiende el CONTENIDO del cambio.

MODO ADVISORIO: nunca falla el build. Si algo sale mal (sin ticket, sin
spec, diff vacio, error de API), se omite sin bloquear nada.

Uso (pensado para correr dentro de un workflow de GitHub Actions):
  python3 scripts/review_agent.py <rama> <pr_numero> <base_ref>

Variables de entorno:
  ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_REPOSITORY
"""

import logging
import os
import re
import subprocess
import sys

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
MAX_DIFF_CHARS = 20000
MARCADOR = "<!-- deploygo-spec-review -->"

SPECS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "specs")

SYSTEM_PROMPT = """Eres un revisor tecnico automatizado. Te dan el spec declarado de
un ticket (que debe cambiar, que NO debe tocarse, el contrato de API, y la
evidencia requerida) y el diff real de un Pull Request. Tu trabajo es decir,
en espanol y en Markdown breve, si el diff respeta lo declarado.

Reglas:
- No inventes problemas que no esten en el diff. Basate solo en lo que ves.
- Si el diff cumple todo, dilo claramente y no busques problemas artificiales.
- Si algo del contrato se rompe, o falta evidencia declarada (ej. tests que
  el spec pide y no aparecen en el diff), senalalo especificamente citando
  el archivo o el fragmento relevante.
- Maximo 8 lineas. Formato: un veredicto (✅ Alineado / ⚠️ Revisar) seguido
  de vinetas con hallazgos concretos, o "Sin observaciones" si no hay nada.
- Nunca digas que apruebas o bloqueas el merge: solo informas a la persona
  que va a revisar. La decision es humana.
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, check=False)


def extraer_ticket(rama):
    # Sin re.IGNORECASE a proposito: la convencion de ramas exige el ticket
    # en mayusculas (ver jira-branch.yml, que usa el mismo criterio en bash).
    # Con IGNORECASE, ramas como 'dependabot/.../checkout-7.0.1' matchean
    # falsamente como ticket "CHECKOUT-7".
    m = re.search(r"[A-Z][A-Z0-9]+-[0-9]+", rama or "")
    return m.group(0) if m else None


def obtener_diff(base_ref):
    r = sh("git", "diff", f"origin/{base_ref}...HEAD")
    if r.returncode != 0:
        logger.warning("git diff fallo: %s", r.stderr.strip())
        return ""
    return r.stdout


def publicar_o_actualizar_comentario(pr_numero, cuerpo):
    repo = os.environ.get("GITHUB_REPOSITORY")
    listado = sh(
        "gh", "api", f"repos/{repo}/issues/{pr_numero}/comments", "--paginate",
        "--jq", f'.[] | select(.body | startswith("{MARCADOR}")) | .id',
    )
    ids = [l.strip() for l in listado.stdout.splitlines() if l.strip()]
    cuerpo_final = f"{MARCADOR}\n{cuerpo}"

    if ids:
        r = sh("gh", "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{ids[0]}", "-f", f"body={cuerpo_final}")
        accion = "actualizado"
    else:
        r = sh("gh", "pr", "comment", str(pr_numero), "--body", cuerpo_final)
        accion = "publicado"

    if r.returncode != 0:
        logger.warning("No se pudo %s el comentario: %s", accion, r.stderr.strip())
    else:
        logger.info("Comentario %s en PR #%s", accion, pr_numero)


def main():
    if len(sys.argv) < 4:
        print("Uso: review_agent.py <rama> <pr_numero> <base_ref>")
        return

    rama, pr_numero, base_ref = sys.argv[1], sys.argv[2], sys.argv[3]
    logger.info("review_agent: rama=%s pr=%s base=%s", rama, pr_numero, base_ref)

    ticket = extraer_ticket(rama)
    if not ticket:
        print(f"ℹ️ Rama '{rama}' sin ticket detectable; se omite el review de IA.")
        return

    spec_path = os.path.join(SPECS_DIR, f"{ticket}.yml")
    if not os.path.isfile(spec_path):
        print(f"ℹ️ No hay specs/{ticket}.yml; se omite el review de IA (nada que comparar).")
        return

    spec = open(spec_path, encoding="utf-8").read()

    diff = obtener_diff(base_ref)
    if not diff.strip():
        print("ℹ️ Diff vacio; se omite el review de IA.")
        return

    truncado = False
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS]
        truncado = True

    mensaje = f"SPEC (specs/{ticket}.yml):\n{spec}\n\nDIFF DEL PR:\n{diff}"
    if truncado:
        mensaje += "\n\n[diff truncado por tamaño, se evaluaron solo los primeros bytes]"

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": mensaje}],
        )
        texto = "".join(b.text for b in resp.content if b.type == "text")
    except Exception as e:
        logger.warning("Fallo llamando a la API de Anthropic: %s", e)
        print(f"⚠️ Fallo llamando a la API de Anthropic: {e}")
        return

    cuerpo = f"### 🤖 Review automatizado contra `specs/{ticket}.yml`\n\n{texto}"
    publicar_o_actualizar_comentario(pr_numero, cuerpo)
    print("✅ Comentario de review publicado/actualizado.")


if __name__ == "__main__":
    main()
