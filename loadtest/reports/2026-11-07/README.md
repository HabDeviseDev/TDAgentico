# Reportes de la etapa 6 (2026-11-07)

Copias de los archivos pequeños de cada ejecución: reconciliación (`reconcile-report*.md/json`), resúmenes de k6 (`k6-summary-*.json`), entorno (`env-*.json`, `stats-*.txt`), parámetros (`meta.json`), sonda del portal y registro de caos. Los datos completos (conjunto, salida por evento de k6, `manifest.csv`) quedan en `loadtest/runs/` y no se versionan.

- `short-02`: prueba corta en frío (54 ambiguos persistidos).
- `short-03`: prueba corta en caliente + sonda del portal.
- `load-300`: 300 vehículos, 50/s, 120 s. `reconcile-report-inicial.md` es la primera reconciliación; `reconcile-report.md`, la repetida tras reiniciar el stack.
- `batch-100`: escenario de lotes.
- `ch-consumer-07031717`, `ch-db-07032716`, `ch-kafka-07033403`: escenarios de caos A, B y C. En C, `reconcile-report-sin-reinicio-api.*` es el resultado antes de la intervención manual.
