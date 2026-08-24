#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
version.py — Calcula la versión semántica (SemVer) del build a partir de los
tags de git y Conventional Commits, para que nadie tenga que escribirla a mano
en cada CICD-DEV / CICD-CERT / Release.

Cómo decide el bump (desde el último tag vX.Y.Z alcanzable hasta HEAD):
  - Cualquier commit con "!" tras el tipo (ej. "feat!:") o con "BREAKING CHANGE"
    en el cuerpo -> sube MAJOR (y corta ahí, no hay nada más alto).
  - Si no hay breaking pero hay algún "feat:" -> sube MINOR.
  - Si no hay "feat:" pero hay al menos un commit -> sube PATCH
    (cubre "fix:", "chore:", y cualquier otro tipo o mensaje sin prefijo).
  - Si no hay commits nuevos desde el tag -> se repite la misma versión.

Si todavía no existe ningún tag "vX.Y.Z" en el repo, arranca desde 0.0.0.

Uso:
  python3 scripts/version.py                  # X.Y.Z (versión de release, sin sufijo)
  python3 scripts/version.py --stage dev      # X.Y.Z-dev.<sha corto>
  python3 scripts/version.py --stage rc       # X.Y.Z-rc.<sha corto>
  python3 scripts/version.py --explain        # además, a stderr: tag base, commits y bump elegido

No depende de librerías externas — solo el binario `git` en PATH.
"""
import argparse
import re
import subprocess
import sys

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BREAKING_RE = re.compile(r"BREAKING CHANGE", re.IGNORECASE)
TYPE_RE = re.compile(r"^(\w+)(\([^)]*\))?(!)?:\s")


def _git(args):
    return subprocess.run(["git"] + args, capture_output=True, text=True, check=False)


def ultimo_tag():
    """Último tag vX.Y.Z alcanzable desde HEAD. None si todavía no hay ninguno."""
    r = _git(["describe", "--tags", "--match", "v[0-9]*.[0-9]*.[0-9]*", "--abbrev=0"])
    if r.returncode != 0:
        return None
    tag = r.stdout.strip()
    return tag if TAG_RE.match(tag) else None


def commits_desde(tag):
    """Mensajes completos (asunto + cuerpo) de los commits entre el tag y HEAD."""
    rango = f"{tag}..HEAD" if tag else "HEAD"
    r = _git(["log", rango, "--pretty=format:%s%n%b%n---COMMIT---"])
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return [c for c in r.stdout.split("---COMMIT---") if c.strip()]


def elegir_bump(commits):
    """'major' | 'minor' | 'patch' | None (sin commits nuevos que ameriten bump)."""
    if not commits:
        return None
    bump = "patch"
    for c in commits:
        primera_linea = c.strip().splitlines()[0] if c.strip() else ""
        m = TYPE_RE.match(primera_linea)
        es_breaking = bool(BREAKING_RE.search(c)) or bool(m and m.group(3) == "!")
        if es_breaking:
            return "major"
        if m and m.group(1) == "feat":
            bump = "minor"
    return bump


def siguiente_version():
    tag = ultimo_tag()
    major, minor, patch = (int(x) for x in TAG_RE.match(tag).groups()) if tag else (0, 0, 0)
    commits = commits_desde(tag)
    bump = elegir_bump(commits)
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    elif bump == "patch":
        patch += 1
    # bump None (tag ya apunta a HEAD, sin commits nuevos) -> se repite la versión del tag
    return f"{major}.{minor}.{patch}", tag, commits, bump


def sha_corto():
    r = _git(["rev-parse", "--short=7", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else "0000000"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["dev", "rc", "release"], default="release",
                     help="dev/rc agregan sufijo de pre-release con el SHA corto; release no.")
    ap.add_argument("--explain", action="store_true", help="Explica el cálculo por stderr.")
    args = ap.parse_args()

    version, tag_base, commits, bump = siguiente_version()

    if args.explain:
        print(f"[version.py] tag base: {tag_base or '(ninguno todavía -- arrancando en 0.0.0)'}", file=sys.stderr)
        print(f"[version.py] commits nuevos desde el tag: {len(commits)}", file=sys.stderr)
        print(f"[version.py] bump elegido: {bump or '(ninguno, se repite la versión)'}", file=sys.stderr)
        print(f"[version.py] versión base calculada: {version}", file=sys.stderr)

    if args.stage == "dev":
        print(f"{version}-dev.{sha_corto()}")
    elif args.stage == "rc":
        print(f"{version}-rc.{sha_corto()}")
    else:
        print(version)


if __name__ == "__main__":
    main()
