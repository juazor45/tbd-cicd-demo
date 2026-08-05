# Estrategia de ramas: Trunk-Based Development (TBD)

## Principio

Todos los desarrolladores integran su trabajo en una única rama principal (`main`, el *trunk*) de forma frecuente — al menos una vez al día — mediante ramas de vida corta y Pull Requests pequeños. El trunk siempre está en estado desplegable.

## Por qué TBD (vs GitFlow)

| | GitFlow | TBD |
|---|---|---|
| Ramas de larga vida | develop, release, hotfix | Solo main |
| Integración | Tardía (merge hell) | Continua (diaria) |
| Feedback de CI | Lento | Inmediato en cada PR |
| Aptitud para CI/CD | Baja | Alta (es el estándar DevOps/DORA) |

## Reglas operativas

1. `main` está protegida: nadie hace push directo.
2. Ramas `feature/*` o `fix/*` nacen de `main` y viven máximo 1–2 días.
3. PRs pequeños con CI obligatorio (quality y security gates) antes del merge.
4. Squash merge para mantener historia lineal y limpia.
5. Funcionalidad incompleta se integra detrás de **feature flags**, nunca en ramas largas.
6. Los despliegues a dev/cert se hacen desde `main` con versión/tag, vía workflow_dispatch.
7. Hotfix: se corrige en `main` y, si hay una versión liberada afectada, se hace cherry-pick al tag de release.

## Versionado

- Semantic Versioning: `MAJOR.MINOR.PATCH`
- DEV: `x.y.z-SNAPSHOT` | CERT: `x.y.z-RC<n>` | PROD: `x.y.z`
- Los tags se crean desde `main` al promover a certificación.
