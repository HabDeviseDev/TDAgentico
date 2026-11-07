# Reconciliación ch-db-07032716

- Generado: 2026-11-07T03:33:06.351625+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "single", "vehicles": 30, "rate": 10.0, "duration_s": 60, "seed": 1, "iterations": 600, "batch_size": null, "planned_requests": 600}
- Planificado: {"counts": {"new": 510, "dup": 60, "invalid": 30}, "unique_identities": 510}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 510, "sent": 510}, "invalid": {"expected_rejection": 30, "sent": 30}, "dup": {"confirmed": 60, "sent": 60}}
- Identidades: {"identities": 540, "confirmed": 510, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 30, "not_sent": 0, "unexpected": 0, "persisted_ok": 510, "vehicles_checked": 30}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 0}, mensajes de la ejecución: 0
- Espera: 0.2 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 1167.789, "p95": 142545.148, "p99": 143751.418}
- Discrepancias: 0
- **Resultado: OK**

