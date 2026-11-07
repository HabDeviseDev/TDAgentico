# Reconciliación short-02

- Generado: 2026-11-07T01:56:34.127815+00:00 · commit `b7cdc62`
- Parámetros: {"mode": "single", "vehicles": 20, "rate": 10.0, "duration_s": 30, "seed": 7, "iterations": 300, "batch_size": null, "planned_requests": 300}
- Planificado: {"counts": {"new": 255, "dup": 30, "invalid": 15}, "unique_identities": 255}
- Ejecutado (por tipo y resultado): {"new": {"ambiguous": 48, "sent": 229, "confirmed": 181, "not_sent": 26}, "invalid": {"expected_rejection": 14, "sent": 14, "not_sent": 1}, "dup": {"ambiguous": 6, "sent": 26, "confirmed": 20, "not_sent": 4}}
- Identidades: {"identities": 270, "confirmed": 185, "ambiguous": 44, "ambiguous_persisted": 44, "invalid": 15, "not_sent": 26, "unexpected": 0, "persisted_ok": 185, "vehicles_checked": 20}
- Ambiguos: 44 (persistidos 44, no persistidos 0)
- DLQ: {"checked": true, "scanned": 17}, mensajes de la ejecución: 0
- Espera: 12.9 s (máx. 90.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 17028.621, "p95": 23177.219, "p99": 23199.963}
- Discrepancias: 0
- **Resultado: OK**

