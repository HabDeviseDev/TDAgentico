# Reconciliación ch-kafka-08001713

- Generado: 2026-11-08T00:32:12.151397+00:00 · commit `4ca1f2c`
- Parámetros: {"mode": "single", "vehicles": 30, "rate": 10.0, "duration_s": 150, "seed": 1, "iterations": 1500, "batch_size": null, "planned_requests": 1500}
- Planificado: {"counts": {"new": 1275, "dup": 150, "invalid": 75}, "unique_identities": 1275}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 1249, "sent": 1250, "ambiguous": 1, "not_sent": 25}, "invalid": {"expected_rejection": 74, "sent": 74, "not_sent": 1}, "dup": {"confirmed": 40, "sent": 147, "ambiguous": 107, "not_sent": 3}}
- Identidades: {"identities": 1350, "confirmed": 1250, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 75, "not_sent": 25, "unexpected": 0, "persisted_ok": 1250, "vehicles_checked": 30}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 0}, mensajes de la ejecución: 0
- Espera: 0.4 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 613.999, "p95": 3409.233, "p99": 30426.473}
- Discrepancias: 0
- **Resultado: OK**

