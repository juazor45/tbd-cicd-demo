# Dashboard de auditoría

_Generado automáticamente el 2026-09-02 16:54 UTC por `scripts/dashboard.py`. No editar a mano — lo regenera `.github/workflows/dashboard.yml`. Panel visual: [`docs/dashboard.html`](dashboard.html)._

| Ticket | Spec | Jira | Pipelines | PRs |
|---|---|---|---|---|
| **SCRUM-14** | [Agregar la divisa GBP al conversor de tipos de cambio](../specs/SCRUM-14.yml) | **QA Testing Done** — sin asignar (hace 163 h) | — sin ejecuciones encontradas — | [Ver PRs](https://github.com/juazor45/tbd-cicd-demo/pulls?q=is%3Apr+SCRUM-14) |

---

## Detalle por ticket

### SCRUM-14 — Agregar la divisa GBP al conversor de tipos de cambio

**Contrato:** GET /api/v1/exchange-rates debe seguir devolviendo el mismo formato JSON (currency, description, buyRate, sellRate, date), solo con GBP agregado a la lista de divisas. GET /api/v1/exchange-rates/{currency}/convert debe aceptar "GBP" igual que acepta USD/EUR/CLP hoy. No cambia el formato de respuesta de ningun endpoint existente.

**Evidencia requerida:**
- Test nuevo que pida GBP en /exchange-rates y valide buyRate/sellRate
- Test nuevo que convierta un monto usando GBP
- CI (ci-pr.yml) en verde
- CICD-DEV corrido y validado-en-dev generado sobre el commit final
