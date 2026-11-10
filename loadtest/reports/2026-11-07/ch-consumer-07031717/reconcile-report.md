# Reconciliación ch-consumer-07031717

- Generado: 2026-11-07T03:26:25.281845+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "single", "vehicles": 30, "rate": 10.0, "duration_s": 60, "seed": 1, "iterations": 600, "batch_size": null, "planned_requests": 600}
- Planificado: {"counts": {"new": 510, "dup": 60, "invalid": 30}, "unique_identities": 510}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 510, "sent": 510}, "invalid": {"expected_rejection": 30, "sent": 30}, "dup": {"confirmed": 60, "sent": 60}}
- Identidades: {"identities": 540, "confirmed": 510, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 30, "not_sent": 0, "unexpected": 0, "persisted_ok": 510, "vehicles_checked": 30}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 0}, mensajes de la ejecución: 0
- Espera: 0.4 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 980.814, "p95": 67338.517, "p99": 69853.962}
- Discrepancias: 0
- **Resultado: OK**

