# Reconciliación batch-100

- Generado: 2026-11-07T02:08:14.860243+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "batch", "vehicles": 100, "rate": 2.0, "duration_s": 60, "seed": 13, "iterations": 120, "batch_size": 25, "planned_requests": 3000}
- Planificado: {"counts": {"new": 2550, "dup": 300, "invalid": 150}, "unique_identities": 2550}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 2550, "sent": 2550}, "invalid": {"expected_rejection": 150, "sent": 150}, "dup": {"confirmed": 300, "sent": 300}}
- Identidades: {"identities": 2700, "confirmed": 2550, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 150, "not_sent": 0, "unexpected": 0, "persisted_ok": 2550, "vehicles_checked": 100}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 17}, mensajes de la ejecución: 0
- Espera: 0.4 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 575.186, "p95": 1191.678, "p99": 1317.574}
- Discrepancias: 0
- **Resultado: OK**

