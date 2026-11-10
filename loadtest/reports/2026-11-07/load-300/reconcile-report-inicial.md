# Reconciliación load-300

- Generado: 2026-11-07T02:04:11.081812+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "single", "vehicles": 300, "rate": 50.0, "duration_s": 120, "seed": 11, "iterations": 6000, "batch_size": null, "planned_requests": 6000}
- Planificado: {"counts": {"new": 5100, "dup": 600, "invalid": 300}, "unique_identities": 5100}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 4941, "sent": 5009, "ambiguous": 68, "not_sent": 91}, "invalid": {"expected_rejection": 295, "sent": 295, "not_sent": 5}, "dup": {"confirmed": 581, "sent": 588, "ambiguous": 7, "not_sent": 12}}
- Identidades: {"identities": 5400, "confirmed": 4950, "ambiguous": 59, "ambiguous_persisted": 59, "invalid": 300, "not_sent": 91, "unexpected": 0, "persisted_ok": 4950, "vehicles_checked": 300}
- Ambiguos: 59 (persistidos 59, no persistidos 0)
- DLQ: {"checked": true, "scanned": 17}, mensajes de la ejecución: 0
- Espera: 77.3 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 848.482, "p95": 1760.844, "p99": 2234.552}
- Discrepancias: 0
- **Resultado: OK**

