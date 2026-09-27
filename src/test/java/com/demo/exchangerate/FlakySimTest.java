package com.demo.exchangerate;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertNotEquals;

/**
 * FlakySimTest -- test DESCARTABLE, no se mergea a main.
 *
 * Simula a proposito un test flaky para probar el Flaky Rerun Pilot (Fase 5,
 * ver agents/flaky-rerun-pilot.yml y scripts/flaky_rerun_pilot.py): falla en
 * el primer intento del run (GITHUB_RUN_ATTEMPT=1) y pasa en el segundo, para
 * verificar que el agente detecta evidencia de flakiness -- el job "ci" ya
 * habia pasado antes en esta misma rama (ver run anterior de CI-PR) -- y
 * dispara el rerun el solo, sin intervencion humana, respetando el limite de
 * 1 reintento por run de policies/agent-governance.yml.
 *
 * Vive solo en la rama de prueba feature/SCRUM-42-prueba-integral -- se
 * elimina en un commit posterior de esta misma rama, antes de mergear.
 */
class FlakySimTest {

    @Test
    void simulaFallaFlakyEnElPrimerIntento() {
        String intento = System.getenv().getOrDefault("GITHUB_RUN_ATTEMPT", "1");
        assertNotEquals("1", intento,
            "Fallo simulado a propósito en el intento " + intento
                + " (para probar el Flaky Rerun Pilot -- Fase 5)");
    }
}
