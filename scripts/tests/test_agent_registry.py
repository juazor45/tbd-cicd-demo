#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent_registry.py -- Pruebas unitarias para scripts/agent_registry.py
(el Agent Registry: manifests declarativos en agents/*.yml).

Cubre tanto los manifests reales del repo (los 6 agentes declarados hoy)
como los mecanismos internos del modulo: el cache en memoria de
manifest_de(), la normalizacion de tools_permitidas cuando el YAML trae un
solo valor en vez de una lista, el ValueError ruidoso ante un manifest
incompleto, y validar_registro() (el chequeo de consistencia contra
tools.py que esta pensado para correr en CI).

Sin dependencias externas -- solo unittest de la libreria estandar. Correr:
    python3 -m unittest discover -s scripts/tests -p "test_*.py" -v
"""

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import agent_registry  # noqa: E402

AGENTES_REALES = {
    "deploy-readiness",
    "flaky-rerun-pilot",
    "policy-advisor",
    "release-status",
    "spec-reviewer",
    "ticket-intake",
}


class TestAgentesDeclarados(unittest.TestCase):
    """agentes_declarados(): ids derivados de los archivos agents/*.yml del repo."""

    def test_lista_los_agentes_reales(self):
        self.assertEqual(set(agent_registry.agentes_declarados()), AGENTES_REALES)

    def test_devuelve_lista_ordenada(self):
        ids = agent_registry.agentes_declarados()
        self.assertEqual(ids, sorted(ids))

    def test_directorio_inexistente_devuelve_lista_vacia(self):
        original = agent_registry.AGENTS_DIR
        agent_registry.AGENTS_DIR = "/no/existe/de/verdad/seguro"
        try:
            self.assertEqual(agent_registry.agentes_declarados(), [])
        finally:
            agent_registry.AGENTS_DIR = original


class TestManifestDeAgentesReales(unittest.TestCase):
    """manifest_de()/tools_de()/nivel_autonomia_de() contra los manifests
    reales -- si alguien cambia un campo en agents/*.yml sin darse cuenta
    (ej. borra tools_permitidas de release-status), esto lo agarra."""

    def test_release_status_es_de_solo_lectura(self):
        m = agent_registry.manifest_de("release-status")
        self.assertEqual(m["nivel_autonomia"], "L1")
        self.assertEqual(m["modo_hitl"], "ninguno")
        self.assertEqual(m["tools_permitidas"], [
            "consultar_jira",
            "consultar_pipelines",
            "detalle_ejecucion",
            "analizar_error_pipeline",
            "consultar_proceso",
        ])

    def test_ticket_intake_es_el_unico_con_tools_de_escritura(self):
        m = agent_registry.manifest_de("ticket-intake")
        self.assertEqual(m["nivel_autonomia"], "L2")
        self.assertEqual(m["modo_hitl"], "approval-before")
        self.assertEqual(set(m["tools_permitidas"]), {"crear_ticket", "comentar_ticket", "listar_tipos_issue"})

    def test_flaky_rerun_pilot_es_el_unico_con_autonomia_l3(self):
        """Guardrail central del proyecto: es el unico agente que EJECUTA
        una accion (re-disparar un job) en vez de solo leer o informar."""
        niveles = {a: agent_registry.nivel_autonomia_de(a) for a in agent_registry.agentes_declarados()}
        agentes_l3 = [a for a, nivel in niveles.items() if nivel == "L3"]
        self.assertEqual(agentes_l3, ["flaky-rerun-pilot"])

    def test_tools_de_delega_en_manifest_de(self):
        self.assertEqual(agent_registry.tools_de("policy-advisor"), ["consultar_politica"])

    def test_manifest_de_usa_cache_en_memoria(self):
        m1 = agent_registry.manifest_de("spec-reviewer")
        m2 = agent_registry.manifest_de("spec-reviewer")
        self.assertIs(m1, m2)  # mismo objeto: la segunda llamada no vuelve a leer el archivo

    def test_manifest_de_agente_inexistente_no_esconde_el_error(self):
        with self.assertRaises(FileNotFoundError):
            agent_registry.manifest_de("agente-que-no-existe-nunca")


class TestManifestValidacionYNormalizacion(unittest.TestCase):
    """Casos que no se pueden probar contra agents/*.yml real (porque hoy
    ningun manifest real esta incompleto ni usa una tool suelta en vez de
    lista) -- se arma un AGENTS_DIR temporal para forzarlos."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="agents-test-")
        self._agents_dir_original = agent_registry.AGENTS_DIR
        agent_registry.AGENTS_DIR = self._tmp_dir
        self._ids_para_limpiar = []

    def tearDown(self):
        agent_registry.AGENTS_DIR = self._agents_dir_original
        for agente_id in self._ids_para_limpiar:
            agent_registry._CACHE.pop(agente_id, None)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _escribir_manifest(self, agente_id, contenido):
        with open(os.path.join(self._tmp_dir, f"{agente_id}.yml"), "w", encoding="utf-8") as f:
            f.write(contenido)
        self._ids_para_limpiar.append(agente_id)

    def test_manifest_incompleto_lanza_valueerror_con_los_campos_faltantes(self):
        self._escribir_manifest("agente-incompleto-test", """
id: agente-incompleto-test
owner: juan.zorrilla
proposito: "le falta tools_permitidas, nivel_autonomia y modo_hitl"
""")
        with self.assertRaises(ValueError) as ctx:
            agent_registry.manifest_de("agente-incompleto-test")
        mensaje = str(ctx.exception)
        self.assertIn("tools_permitidas", mensaje)
        self.assertIn("nivel_autonomia", mensaje)
        self.assertIn("modo_hitl", mensaje)

    def test_tools_permitidas_con_un_solo_valor_se_normaliza_a_lista(self):
        """El parser minimo deja 'tools_permitidas: una_sola_tool' (sin '- ')
        como string suelto, no como lista de un elemento -- manifest_de()
        tiene que corregir eso para que tools_de() siempre devuelva una lista."""
        self._escribir_manifest("agente-una-tool-test", """
id: agente-una-tool-test
owner: juan.zorrilla
proposito: "una sola tool permitida, sin guion en el YAML"
tools_permitidas: consultar_politica
nivel_autonomia: L1
modo_hitl: ninguno
""")
        m = agent_registry.manifest_de("agente-una-tool-test")
        self.assertEqual(m["tools_permitidas"], ["consultar_politica"])
        self.assertEqual(agent_registry.tools_de("agente-una-tool-test"), ["consultar_politica"])


class TestValidarRegistro(unittest.TestCase):
    """validar_registro(): chequeo de consistencia entre agents/*.yml y las
    tools que existen de verdad en tools.py. Pensado para correr en CI."""

    def test_el_registro_real_del_repo_no_tiene_problemas(self):
        """Si esto falla, algun manifest en agents/*.yml quedo declarando una
        tool que ya no existe (o nunca existio) en tools.py -- exactamente
        el tipo de typo silencioso que este chequeo existe para agarrar."""
        problemas = agent_registry.validar_registro()
        self.assertEqual(problemas, [])

    def test_detecta_una_tool_que_no_existe_en_tools_py(self):
        tmp_dir = tempfile.mkdtemp(prefix="agents-validar-test-")
        original_dir = agent_registry.AGENTS_DIR
        agent_registry.AGENTS_DIR = tmp_dir
        try:
            with open(os.path.join(tmp_dir, "agente-con-tool-fantasma-test.yml"), "w", encoding="utf-8") as f:
                f.write("""
id: agente-con-tool-fantasma-test
owner: juan.zorrilla
proposito: "declara una tool que no existe en tools.py, a proposito"
tools_permitidas:
  - esta_tool_no_existe_en_tools_py
nivel_autonomia: L1
modo_hitl: ninguno
""")
            problemas = agent_registry.validar_registro()
            self.assertTrue(any("esta_tool_no_existe_en_tools_py" in p for p in problemas))
        finally:
            agent_registry.AGENTS_DIR = original_dir
            agent_registry._CACHE.pop("agente-con-tool-fantasma-test", None)
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
