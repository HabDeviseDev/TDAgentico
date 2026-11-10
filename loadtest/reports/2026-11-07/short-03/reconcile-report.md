# Reconciliación short-03

- Generado: 2026-11-07T01:56:52.414369+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "single", "vehicles": 20, "rate": 10.0, "duration_s": 30, "seed": 7, "iterations": 300, "batch_size": null, "planned_requests": 300}
- Planificado: {"counts": {"new": 255, "dup": 30, "invalid": 15}, "unique_identities": 255}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 254, "sent": 254, "not_sent": 1}, "invalid": {"expected_rejection": 15, "sent": 15}, "dup": {"confirmed": 30, "sent": 30}}
- Identidades: {"identities": 270, "confirmed": 254, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 15, "not_sent": 1, "unexpected": 0, "persisted_ok": 254, "vehicles_checked": 20}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 17}, mensajes de la ejecución: 0
- Espera: 0.3 s (máx. 90.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 696.406, "p95": 1229.405, "p99": 1598.754}
- Discrepancias: 0
- **Resultado: OK**

