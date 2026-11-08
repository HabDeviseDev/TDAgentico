# Reportes de la etapa 6 (2026-11-07)

Copias de los archivos pequeños de cada ejecución: reconciliación (`reconcile-report*.md/json`), resúmenes de k6 (`k6-summary-*.json`), entorno (`env-*.json`, `stats-*.txt`), parámetros (`meta.json`), sonda del portal y registro de caos. Los datos completos (conjunto, salida por evento de k6, `manifest.csv`) quedan en `loadtest/runs/` y no se versionan.

- `short-02`: prueba corta en frío (54 ambiguos persistidos).
- `short-03`: prueba corta en caliente + sonda del portal.
- `load-300`: 300 vehículos, 50/s, 120 s. `reconcile-report-inicial.md` es la primera reconciliación; `reconcile-report.md`, la repetida tras reiniciar el stack.
- `batch-100`: escenario de lotes.
- `ch-consumer-07031717`, `ch-db-07032716`, `ch-kafka-07033403`: escenarios de caos A, B y C. En C, `reconcile-report-sin-reinicio-api.*` es el resultado antes de la intervención manual.
- `ch-kafka-07235854`, `ch-kafka-08001713`: escenario C repetido con el reemplazo automático del productor de la API (`DURATION=150 FAULT_AT=10 FAULT_FOR=40`). `reconcile-before-resend.txt` es la reconciliación previa al reenvío; `api-log-resumen.txt` (solo en la segunda) cuenta las líneas del log de la API, sin las de eventos aceptados, con los `event_id` enmascarados.
