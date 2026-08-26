#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — Configuración de autenticación del bot, leída de variables de
entorno (Application Settings de la Function App).

Con auth-type Managed Identity (el que usa setup-azure.sh), MicrosoftAppType
es "UserAssignedMSI", MicrosoftAppId es el CLIENT ID de la User-Assigned
Managed Identity asignada a la Function App (no el de la Function App en sí),
y MicrosoftAppPassword queda vacío -- no hace falta ningún secreto guardado.

Nombres de variables iguales a los que usan las muestras oficiales de Bot
Framework, para que cualquiera que siga la documentación de Microsoft
reconozca el patrón.
"""

import os


class DefaultConfig:
    APP_TYPE = os.environ.get("MicrosoftAppType", "MultiTenant")
    APP_ID = os.environ.get("MicrosoftAppId", "")
    APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
    APP_TENANTID = os.environ.get("MicrosoftAppTenantId", "")
