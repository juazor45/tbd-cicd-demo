#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
http_client.py — Cliente HTTP compartido para Jira y GitHub.

Usado por tools.py (el agente) y release-status.py (el reporte de
GitHub Actions), para no duplicar la lógica de autenticación, el
manejo de SSL y el formateo de fechas relativas.

Sin dependencias externas: solo librería estándar de Python 3.8+.
"""

import base64
import json
import logging
import os
import ssl
import time
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# En Windows corporativo (schannel/proxy) puede fallar la revocación de certificados.
# Si ocurre, exporta SSL_NO_VERIFY=1 solo para pruebas locales.
SSL_CTX = ssl.create_default_context()
if os.environ.get("SSL_NO_VERIFY") == "1":
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE


def http_get(url, headers=None):
    """GET genérico. Devuelve (status_code, dict). status 0 = error de conexión/parseo.

    Loguea cada llamada (URL, status, duración) para poder auditar si Jira/GitHub
    respondieron. Nunca loguea los headers: ahí va la autenticación.
    """
    inicio = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            ms = int((time.monotonic() - inicio) * 1000)
            logger.info("GET %s -> %s (%d ms)", url, resp.status, ms)
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.warning("GET %s -> %s (%d ms)", url, e.code, ms)
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.error("GET %s -> error de conexión tras %d ms: %s", url, ms, e)
        return 0, {"error": str(e)}


def jira_headers():
    """Headers de autenticación Basic para la API de Jira (JIRA_EMAIL + JIRA_API_TOKEN)."""
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Accept": "application/json"}


def gh_headers(user_agent="deploygo-assistant"):
    """Headers para la API de GitHub. GITHUB_TOKEN es opcional en repos públicos."""
    h = {"Accept": "application/vnd.github+json", "User-Agent": user_agent}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def age(iso):
    """Formatea un timestamp ISO 8601 como 'hace N min' / 'hace N h'."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        m = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        return f"hace {m} min" if m < 120 else f"hace {m // 60} h"
    except Exception:
        return "fecha desconocida"
