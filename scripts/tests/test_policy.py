#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_policy.py -- Pruebas unitarias para scripts/policy.py (el motor de
politicas / Policy Decision Point).

Cubre las reglas reales declaradas en policies/*.yml (deploy-policy,
agent-governance, bot-policy) contra contextos representativos, ademas de
los mecanismos internos del motor (resolucion de templates "{{ }}", manejo
de un predicado desconocido, la propiedad Decision.permitido).

Sin dependencias externas -- solo unittest de la libreria estandar, misma
filosofia que el resto del proyecto. Se puede correr:
    python3 -m unittest discover -s scripts/tests -p "test_*.py" -v
o directamente:
    python3 scripts/tests/test_policy.py
"""

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import policy  # noqa: E402


class TestResolver(unittest.TestCase):
    """_resolver: templates '{{ a.b.c }}' contra el contexto, o valor literal."""

    def test_resuelve_ruta_anidada(self):
        contexto = {"jira": {"estado": "Construcción Done"}}
        self.assertEqual(policy._resolver("{{ jira.estado }}", contexto), "Construcción Done")

    def test_valor_literal_pasa_igual(self):
        contexto = {}
        self.assertEqual(policy._resolver("main", contexto), "main")
        self.assertEqual(policy._resolver(["a", "b"], contexto), ["a", "b"])

    def test_ruta_inexistente_devuelve_none(self):
        contexto = {"jira": {"estado": "Doing"}}
        self.assertIsNone(policy._resolver("{{ jira.otro_campo }}", contexto))

    def test_ruta_sobre_no_dict_devuelve_none(self):
        contexto = {"jira": "no-es-un-dict"}
        self.assertIsNone(policy._resolver("{{ jira.estado }}", contexto))


class TestCargarYamlSimple(unittest.TestCase):
    """El parser minimo debe leer correctamente los policies/*.yml reales
    del repo (no un YAML generico, sino la forma fija que usan estos
    archivos: mapas anidados, listas de escalares, listas de mapas)."""

    def test_carga_deploy_policy_real(self):
        politica = policy.cargar_yaml_simple(os.path.join(policy.POLICIES_DIR, "deploy-policy.yml"))
        self.assertEqual(politica["policy"], "deploy-policy")
        self.assertEqual(politica["enforcement"], "blocking")
        self.assertEqual(len(politica["rules"]), 2)
        ids = [r["id"] for r in politica["rules"]]
        self.assertIn("rama-permitida", ids)
        self.assertIn("estado-jira-requerido", ids)

    def test_carga_lista_de_escalares_anidada(self):
        politica = policy.cargar_yaml_simple(os.path.join(policy.POLICIES_DIR, "deploy-policy.yml"))
        regla_estado = next(r for r in politica["rules"] if r["id"] == "estado-jira-requerido")
        estados = regla_estado["require"]["jira_estado_en"]
        self.assertEqual(estados, ["Construcción Done", "Congelamiento Doing"])

    def test_carga_agent_governance_real(self):
        politica = policy.cargar_yaml_simple(os.path.join(policy.POLICIES_DIR, "agent-governance.yml"))
        self.assertEqual(politica["max_reintentos_por_run"], "1")
        self.assertEqual(len(politica["rules"]), 6)


class TestEvaluarDeployPolicy(unittest.TestCase):
    """deploy-policy.yml: rama-permitida (rama_es: main) y
    estado-jira-requerido (jira_estado_en: [...])."""

    def test_contexto_valido_no_tiene_violaciones(self):
        decision = policy.evaluar("deploy-policy", {
            "rama": "main",
            "jira_estado": "Construcción Done",
        })
        self.assertTrue(decision.permitido)
        self.assertEqual(decision.violaciones, [])

    def test_rama_distinta_de_main_bloquea(self):
        decision = policy.evaluar("deploy-policy", {
            "rama": "feature/SCRUM-1-algo",
            "jira_estado": "Construcción Done",
        })
        self.assertFalse(decision.permitido)
        ids_violados = [v["regla_id"] for v in decision.violaciones]
        self.assertIn("rama-permitida", ids_violados)

    def test_estado_jira_no_valido_bloquea(self):
        decision = policy.evaluar("deploy-policy", {
            "rama": "main",
            "jira_estado": "Backlog",
        })
        self.assertFalse(decision.permitido)
        ids_violados = [v["regla_id"] for v in decision.violaciones]
        self.assertIn("estado-jira-requerido", ids_violados)

    def test_enforcement_es_blocking(self):
        decision = policy.evaluar("deploy-policy", {"rama": "main", "jira_estado": "Construcción Done"})
        self.assertEqual(decision.enforcement, "blocking")


class TestEvaluarAgentGovernance(unittest.TestCase):
    """agent-governance.yml: las 6 barreras del piloto de autonomia L3
    (flaky-rerun-pilot). Un contexto que las cumple todas debe pasar; cada
    una de ellas, rota individualmente, debe bloquear con su propio id."""

    def _contexto_valido(self, **overrides):
        base = {
            "agente_id": "flaky-rerun-pilot",
            "accion": "rerun_job",
            "workflow": "CI-PR",
            "todos_los_fallidos_son_ci": True,
            "reintentos_dentro_del_limite": True,
            "job_paso_antes": True,
        }
        base.update(overrides)
        return base

    def test_contexto_completo_y_valido_pasa(self):
        decision = policy.evaluar("agent-governance", self._contexto_valido())
        self.assertTrue(decision.permitido, decision.violaciones)

    def test_agente_no_autorizado_bloquea(self):
        decision = policy.evaluar("agent-governance", self._contexto_valido(agente_id="otro-agente"))
        self.assertFalse(decision.permitido)
        self.assertIn("agente-autorizado", [v["regla_id"] for v in decision.violaciones])

    def test_workflow_cicd_cert_nunca_autorizado(self):
        """Guardrail central de esta politica: CICD-CERT jamas puede
        disparar un rerun automatico (ahi solo corre deploy-readiness, que
        unicamente lee)."""
        decision = policy.evaluar("agent-governance", self._contexto_valido(workflow="CICD-CERT"))
        self.assertFalse(decision.permitido)
        self.assertIn("workflow-autorizado", [v["regla_id"] for v in decision.violaciones])

    def test_job_de_deploy_entre_los_fallidos_bloquea(self):
        decision = policy.evaluar("agent-governance", self._contexto_valido(todos_los_fallidos_son_ci=False))
        self.assertFalse(decision.permitido)
        self.assertIn("solo-jobs-de-test", [v["regla_id"] for v in decision.violaciones])

    def test_limite_de_reintentos_alcanzado_bloquea(self):
        decision = policy.evaluar("agent-governance", self._contexto_valido(reintentos_dentro_del_limite=False))
        self.assertFalse(decision.permitido)
        self.assertIn("dentro-del-limite-de-reintentos", [v["regla_id"] for v in decision.violaciones])

    def test_sin_evidencia_de_flaky_bloquea(self):
        decision = policy.evaluar("agent-governance", self._contexto_valido(job_paso_antes=False))
        self.assertFalse(decision.permitido)
        self.assertIn("evidencia-de-flaky", [v["regla_id"] for v in decision.violaciones])

    def test_reglas_seleccionadas_solo_evalua_esas(self):
        """El parametro 'reglas' permite evaluar un subconjunto (asi lo usa
        bot-policy segun el punto del flujo) -- una violacion fuera del
        subconjunto pedido no debe aparecer."""
        contexto = self._contexto_valido(agente_id="otro-agente")  # violaria agente-autorizado
        decision = policy.evaluar("agent-governance", contexto, reglas=["workflow-autorizado"])
        self.assertTrue(decision.permitido)


class TestEvaluarBotPolicy(unittest.TestCase):
    """bot-policy.yml: incluye un valor con template '{{ canal_permitido }}'
    que se resuelve contra el propio contexto antes de comparar."""

    def test_canal_autorizado_resuelve_template_y_pasa(self):
        decision = policy.evaluar("bot-policy", {
            "rate_limitado": False,
            "canal_actual": "C123",
            "canal_permitido": "C123",
            "usuario_actual": "juan",
            "usuario_inicio": "juan",
        }, reglas=["canal-autorizado"])
        self.assertTrue(decision.permitido)

    def test_canal_distinto_bloquea(self):
        decision = policy.evaluar("bot-policy", {
            "canal_actual": "C999",
            "canal_permitido": "C123",
        }, reglas=["canal-autorizado"])
        self.assertFalse(decision.permitido)
        self.assertIn("canal-autorizado", [v["regla_id"] for v in decision.violaciones])

    def test_rate_limit_excedido_bloquea(self):
        decision = policy.evaluar("bot-policy", {"rate_limitado": True}, reglas=["sin-rate-limit"])
        self.assertFalse(decision.permitido)

    def test_confirmacion_de_otro_usuario_bloquea(self):
        decision = policy.evaluar("bot-policy", {
            "usuario_actual": "otro",
            "usuario_inicio": "juan",
        }, reglas=["confirmado-por-mismo-usuario"])
        self.assertFalse(decision.permitido)


class TestMotorInterno(unittest.TestCase):
    """Mecanismos del motor que no dependen de una politica real del repo:
    predicado desconocido (typo en policies/*.yml) y la propiedad
    Decision.permitido. Usa un directorio temporal de policies para no
    tocar los archivos reales."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="policy-test-")
        self._policies_dir_original = policy.POLICIES_DIR
        policy.POLICIES_DIR = self._tmp_dir

    def tearDown(self):
        policy.POLICIES_DIR = self._policies_dir_original
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _escribir_politica(self, nombre, contenido):
        with open(os.path.join(self._tmp_dir, f"{nombre}.yml"), "w", encoding="utf-8") as f:
            f.write(contenido)

    def test_predicado_desconocido_se_registra_como_violacion_sin_crashear(self):
        self._escribir_politica("con-typo", """
policy: con-typo
version: 1
enforcement: advisory
rules:
  - id: regla-con-typo
    require:
      predicado_que_no_existe: true
    on_fail:
      mensaje: "no deberia importar, el predicado ni existe"
""")
        decision = policy.evaluar("con-typo", {})
        self.assertFalse(decision.permitido)
        self.assertIn("predicado desconocido", decision.violaciones[0]["mensaje"])

    def test_politica_advisory_no_bloquea_pero_reporta(self):
        self._escribir_politica("solo-advisory", """
policy: solo-advisory
version: 1
enforcement: advisory
rules:
  - id: siempre-falla
    require:
      rama_es: nunca-va-a-matchear
    on_fail:
      mensaje: "esto siempre falla a proposito"
""")
        decision = policy.evaluar("solo-advisory", {"rama": "main"})
        self.assertEqual(decision.enforcement, "advisory")
        self.assertFalse(decision.permitido)
        self.assertEqual(len(decision.violaciones), 1)

    def test_decision_repr_no_crashea(self):
        decision = policy.Decision("x", "blocking")
        self.assertIn("OK", repr(decision))
        decision.violaciones.append({"regla_id": "y", "mensaje": "z", "detalle": []})
        self.assertIn("violacion", repr(decision))


if __name__ == "__main__":
    unittest.main(verbosity=2)
