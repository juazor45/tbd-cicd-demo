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


class _SinAuthEnRedirect(urllib.request.HTTPRedirectHandler):
    """Corta el auto-seguimiento de redirects de urllib para no reenviar el
    header Authorization original al destino.

    GitHub redirige el log de un job (302) a una URL PRE-FIRMADA de blob
    storage (SAS token en la query string) -- esa URL ya trae su propia
    autenticación. Si dejamos que urllib reenvíe el mismo header
    Authorization ahí (su comportamiento por default), blob storage ve dos
    mecanismos de auth en conflicto y devuelve 401 -- confirmado en vivo
    contra un job real: la MISMA URL de redirect responde 200 sin el header
    y 401 con él puesto. Por eso paramos acá y hacemos el segundo GET
    nosotros mismos, sin headers, en http_get_text.
    """

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None


def http_get_text(url, headers=None, max_chars=15000):
    """GET que devuelve TEXTO plano en vez de JSON. Pensado para los logs de
    GitHub Actions (GET /repos/.../actions/jobs/{job_id}/logs), que GitHub
    sirve via un redirect 302 a texto plano en blob storage -- no son JSON,
    asi que http_get() rompería con json.loads().

    OJO: el segundo GET (al destino del redirect) se hace SIN los headers
    originales -- ver _SinAuthEnRedirect. No es un seguimiento de redirect
    genérico: es específico para este patrón de "redirect a URL pre-firmada".

    Devuelve (status_code, texto). Si el texto supera max_chars, se queda con
    el FINAL (donde suele estar el error real y el stack trace, no el
    principio del build) y antepone un aviso de truncado.
    """
    inicio = time.monotonic()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=SSL_CTX), _SinAuthEnRedirect()
    )
    try:
        req = urllib.request.Request(url, headers=headers or {})
        try:
            resp = opener.open(req, timeout=30)
            status, crudo = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not location:
                raise
            req2 = urllib.request.Request(location)
            with urllib.request.urlopen(req2, context=SSL_CTX, timeout=30) as resp2:
                status, crudo = resp2.status, resp2.read()
        ms = int((time.monotonic() - inicio) * 1000)
        logger.info("GET(text) %s -> %s (%d bytes, %d ms)", url, status, len(crudo), ms)
        texto = crudo.decode("utf-8", errors="replace")
        if len(texto) > max_chars:
            texto = f"[...log truncado, mostrando los últimos {max_chars} caracteres...]\n" + texto[-max_chars:]
        return status, texto
    except urllib.error.HTTPError as e:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.warning("GET(text) %s -> %s (%d ms)", url, e.code, ms)
        return e.code, ""
    except Exception as e:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.error("GET(text) %s -> error de conexión tras %d ms: %s", url, ms, e)
        return 0, ""


def http_post(url, headers=None, body=None):
    """POST generico con cuerpo JSON. Devuelve (status_code, dict). status 0 = error
    de conexion/parseo. Mismo criterio que http_get: nunca truena, siempre informa
    que paso. Nunca loguea el cuerpo (puede traer texto libre de un usuario) ni los
    headers (ahi va la autenticacion) -- solo metodo, URL, status y duracion.
    """
    inicio = time.monotonic()
    payload = json.dumps(body or {}).encode("utf-8")
    envio = {**(headers or {}), "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=payload, headers=envio, method="POST")
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            ms = int((time.monotonic() - inicio) * 1000)
            logger.info("POST %s -> %s (%d ms)", url, resp.status, ms)
            crudo = resp.read().decode("utf-8")
            return resp.status, (json.loads(crudo) if crudo.strip() else {})
    except urllib.error.HTTPError as e:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.warning("POST %s -> %s (%d ms)", url, e.code, ms)
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.error("POST %s -> error de conexion tras %d ms: %s", url, ms, e)
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
