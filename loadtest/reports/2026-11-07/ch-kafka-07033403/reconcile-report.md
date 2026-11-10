# Reconciliación ch-kafka-07033403

- Generado: 2026-11-07T03:43:58.851383+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "single", "vehicles": 30, "rate": 10.0, "duration_s": 60, "seed": 1, "iterations": 600, "batch_size": null, "planned_requests": 600}
- Planificado: {"counts": {"new": 510, "dup": 60, "invalid": 30}, "unique_identities": 510}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 492, "sent": 492, "not_sent": 18}, "invalid": {"expected_rejection": 29, "sent": 29, "not_sent": 1}, "dup": {"confirmed": 42, "sent": 58, "ambiguous": 16, "not_sent": 2}}
- Identidades: {"identities": 540, "confirmed": 492, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 30, "not_sent": 18, "unexpected": 0, "persisted_ok": 492, "vehicles_checked": 30}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 0}, mensajes de la ejecución: 0
- Espera: 0.1 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 663.046, "p95": 1515.396, "p99": 1953.928}
- Discrepancias: 0
- **Resultado: OK**

