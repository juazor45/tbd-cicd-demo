#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_validate_spec.py -- Pruebas unitarias para scripts/validate_spec.py
(valida que un PR se mantenga dentro de specs/<TICKET>.yml).

Cubre las tres funciones puras del modulo (extraer_ticket, parsear_spec,
_formatear_violacion) y, de punta a punta, main() contra el spec real
specs/SCRUM-14.yml -- sin mockear policy.py: la prueba de integracion usa
el motor de politicas real y policies/spec-compliance.yml real, exactamente
como corre en ci-pr.yml.

Sin dependencias externas -- solo unittest de la libreria estandar. Correr:
    python3 -m unittest discover -s scripts/tests -p "test_*.py" -v
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import validate_spec  # noqa: E402


class TestExtraerTicket(unittest.TestCase):
    """extraer_ticket(): busca un patron TICKET-123 en el nombre de rama,
    deliberadamente case-sensitive (ver docstring del modulo: con
    IGNORECASE, una rama de dependabot como '.../checkout-7.0.1' matchearia
    falsamente como ticket 'CHECKOUT-7')."""

    def test_extrae_ticket_de_rama_feature_estandar(self):
        self.assertEqual(validate_spec.extraer_ticket("feature/SCRUM-19-analizar-error-pipeline"), "SCRUM-19")

    def test_extrae_ticket_con_numero_de_varios_digitos(self):
        self.assertEqual(validate_spec.extraer_ticket("feature/SCRUM-142-algo"), "SCRUM-142")

    def test_rama_sin_ticket_devuelve_none(self):
        self.assertIsNone(validate_spec.extraer_ticket("chore/add-tests-and-license"))

    def test_rama_de_dependabot_en_minusculas_no_matchea_falsamente(self):
        """El caso exacto que motiva no usar re.IGNORECASE."""
        self.assertIsNone(validate_spec.extraer_ticket("dependabot/github_actions/actions/checkout-7.0.1"))

    def test_rama_none_devuelve_none(self):
        self.assertIsNone(validate_spec.extraer_ticket(None))

    def test_rama_vacia_devuelve_none(self):
        self.assertIsNone(validate_spec.extraer_ticket(""))

    def test_con_dos_posibles_tickets_devuelve_el_primero(self):
        self.assertEqual(validate_spec.extraer_ticket("merge/SCRUM-1-vs-SCRUM-2"), "SCRUM-1")


class TestParsearSpec(unittest.TestCase):
    """parsear_spec(): parser minimo (escalares, listas, bloques folded '>')
    contra el spec real specs/SCRUM-14.yml del repo."""

    def setUp(self):
        self.spec = validate_spec.parsear_spec(
            os.path.join(validate_spec.SPECS_DIR, "SCRUM-14.yml")
        )

    def test_escalares(self):
        self.assertEqual(self.spec["ticket"], "SCRUM-14")
        self.assertEqual(self.spec["titulo"], "Agregar la divisa GBP al conversor de tipos de cambio")

    def test_listas(self):
        self.assertEqual(self.spec["cambios_permitidos"], [
            "src/main/java/com/demo/exchangerate/**",
            "src/test/java/com/demo/exchangerate/**",
        ])
        self.assertEqual(self.spec["cambios_prohibidos"], [
            "pom.xml",
            "Dockerfile",
            ".github/workflows/**",
        ])
        self.assertEqual(len(self.spec["evidencia_requerida"]), 4)

    def test_bloque_folded_se_une_en_una_sola_linea(self):
        contrato = self.spec["contrato"]
        self.assertNotIn("\n", contrato)
        self.assertTrue(contrato.startswith("GET /api/v1/exchange-rates debe seguir devolviendo"))
        self.assertIn("No cambia el formato de respuesta de ningun endpoint existente.", contrato)

    def test_items_de_lista_entre_comillas_pierden_las_comillas(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as f:
            f.write('ticket: SCRUM-0\ncambios_permitidos:\n  - "un item entre comillas dobles"\n  - \'otro entre simples\'\n')
            path = f.name
        try:
            spec = validate_spec.parsear_spec(path)
            self.assertEqual(spec["cambios_permitidos"], [
                "un item entre comillas dobles",
                "otro entre simples",
            ])
        finally:
            os.unlink(path)

    def test_lineas_vacias_y_comentarios_se_ignoran(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as f:
            f.write("# esto es un comentario\nticket: SCRUM-0\n\ntitulo: \"algo\"\n")
            path = f.name
        try:
            spec = validate_spec.parsear_spec(path)
            self.assertEqual(spec["ticket"], "SCRUM-0")
            self.assertEqual(spec["titulo"], "algo")
        finally:
            os.unlink(path)


class TestFormatearViolacion(unittest.TestCase):
    """_formatear_violacion(): traduce una violacion de Decision al texto
    legible que se imprime en el step de ci-pr.yml."""

    def test_violacion_con_patron_prohibido(self):
        v = {
            "regla_id": "archivos-prohibidos",
            "mensaje": "coincide con un patron PROHIBIDO",
            "detalle": [{"archivo": "pom.xml", "patron": "pom.xml"}],
        }
        lineas = validate_spec._formatear_violacion(v)
        self.assertEqual(lineas, ["❌ 'pom.xml' coincide con un patron PROHIBIDO ('pom.xml')"])

    def test_violacion_fuera_de_cambios_permitidos(self):
        v = {
            "regla_id": "archivos-permitidos",
            "mensaje": "no esta en cambios_permitidos",
            "detalle": [{"archivo": "otro/archivo.py"}],
        }
        lineas = validate_spec._formatear_violacion(v)
        self.assertEqual(lineas, ["⚠️ 'otro/archivo.py' no esta en cambios_permitidos"])

    def test_violacion_con_varios_archivos_en_el_detalle(self):
        v = {
            "regla_id": "archivos-permitidos",
            "mensaje": "no esta en cambios_permitidos",
            "detalle": [{"archivo": "a.py"}, {"archivo": "b.py"}],
        }
        lineas = validate_spec._formatear_violacion(v)
        self.assertEqual(len(lineas), 2)


class TestMainIntegracion(unittest.TestCase):
    """main() de punta a punta, sin mockear policy.py -- usa el motor de
    politicas real y policies/spec-compliance.yml real, tal como corre
    dentro de ci-pr.yml."""

    def _correr(self, rama, archivos):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(archivos))
            diff_path = f.name
        old_argv = sys.argv
        sys.argv = ["validate_spec.py", rama, diff_path]
        buf = io.StringIO()
        codigo_salida = None
        try:
            with contextlib.redirect_stdout(buf):
                validate_spec.main()
        except SystemExit as e:
            codigo_salida = e.code
        finally:
            sys.argv = old_argv
            os.unlink(diff_path)
        return codigo_salida, buf.getvalue()

    def test_rama_sin_ticket_se_omite_sin_error(self):
        codigo, salida = self._correr("chore/sin-ticket", ["algo.txt"])
        self.assertIsNone(codigo)
        self.assertIn("no tiene ticket detectable", salida)

    def test_ticket_sin_spec_declarado_avisa_y_no_falla(self):
        codigo, salida = self._correr("feature/SCRUM-999999-sin-spec", ["algo.txt"])
        self.assertIsNone(codigo)
        self.assertIn("No existe specs/SCRUM-999999.yml", salida)

    def test_archivo_dentro_de_lo_permitido_pasa(self):
        codigo, salida = self._correr(
            "feature/SCRUM-14-gbp",
            ["src/main/java/com/demo/exchangerate/ExchangeRateResource.java"],
        )
        self.assertIsNone(codigo)
        self.assertIn("El PR se mantiene dentro de los limites declarados en el spec", salida)

    def test_archivo_prohibido_avisa_pero_no_bloquea_porque_es_advisory(self):
        """spec-compliance.yml tiene enforcement: advisory -- debe reportar
        la violacion (dos veces: no esta permitido Y esta prohibido) pero
        jamas cortar el build con sys.exit(1)."""
        codigo, salida = self._correr("feature/SCRUM-14-gbp", ["pom.xml"])
        self.assertIsNone(codigo)
        self.assertIn("coincide con un patron PROHIBIDO", salida)
        self.assertIn("no esta en cambios_permitidos", salida)
        self.assertIn("ADVISORIO (no bloquea)", salida)

    def test_el_propio_spec_del_ticket_esta_excluido_de_la_validacion(self):
        """Todo PR puede crear/actualizar su propio specs/<TICKET>.yml sin
        que eso mismo dispare una violacion (ver 'excluir' en main())."""
        codigo, salida = self._correr("feature/SCRUM-14-gbp", ["specs/SCRUM-14.yml"])
        self.assertIsNone(codigo)
        self.assertIn("El PR se mantiene dentro de los limites declarados en el spec", salida)

    def test_pocos_argumentos_imprime_uso_y_sale_con_codigo_0(self):
        old_argv = sys.argv
        sys.argv = ["validate_spec.py", "solo-una-rama"]
        buf = io.StringIO()
        try:
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stdout(buf):
                    validate_spec.main()
            self.assertEqual(ctx.exception.code, 0)
            self.assertIn("Uso:", buf.getvalue())
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main(verbosity=2)
