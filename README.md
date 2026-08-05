# ms-exchange-rate (Quarkus)

Microservicio demo de tipos de cambio contra el Sol peruano (PEN), construido con **Quarkus 3 + Java 17 + Maven**. Parte del repo de prueba TBD + CI/CD.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/exchange-rates` | Lista todas las divisas (USD, EUR, CLP) |
| GET | `/api/v1/exchange-rates/{currency}` | Tipo de cambio de una divisa (404 si no existe) |
| GET | `/api/v1/exchange-rates/{currency}/convert?amount=100` | Convierte un monto a PEN (400 sin amount) |
| GET | `/q/health/live` / `/q/health/ready` | Probes para Kubernetes |

## Desarrollo local

```bash
# Modo dev con live reload (la joya de Quarkus)
mvn quarkus:dev

# Probar
curl localhost:8080/api/v1/exchange-rates
curl localhost:8080/api/v1/exchange-rates/USD/convert?amount=100

# Tests + coverage (JaCoCo en target/jacoco-report)
mvn verify

# Empaquetar (fast-jar en target/quarkus-app/)
mvn package
java -jar target/quarkus-app/quarkus-run.jar

# Imagen Docker
docker build -t ms-exchange-rate:local .
```

## Pipelines

- `ci-pr.yml`: se dispara en cada PR hacia `main` → `mvn verify` real (gate de TBD).
- `cicd-dev.yml` / `cicd-cert.yml`: `workflow_dispatch` con CI (build real + gates mockeados de SonarQube/Fortify/Xray/Vault) y CD (docker build real + deploy mockeado a K8s). Cert incluye validación del ticket de cambio en Jira y approval gate vía environment.
