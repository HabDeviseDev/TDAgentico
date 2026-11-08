# Reconciliación ch-kafka-07235854

- Generado: 2026-11-08T00:10:37.727876+00:00 · commit `4ca1f2c`
- Parámetros: {"mode": "single", "vehicles": 30, "rate": 10.0, "duration_s": 150, "seed": 1, "iterations": 1500, "batch_size": null, "planned_requests": 1500}
- Planificado: {"counts": {"new": 1275, "dup": 150, "invalid": 75}, "unique_identities": 1275}
- Ejecutado (por tipo y resultado): {"new": {"confirmed": 1241, "sent": 1242, "ambiguous": 1, "not_sent": 33}, "invalid": {"expected_rejection": 73, "sent": 73, "not_sent": 2}, "dup": {"confirmed": 52, "sent": 146, "ambiguous": 94, "not_sent": 4}}
- Identidades: {"identities": 1350, "confirmed": 1242, "ambiguous": 0, "ambiguous_persisted": 0, "invalid": 75, "not_sent": 33, "unexpected": 0, "persisted_ok": 1242, "vehicles_checked": 30}
- Ambiguos: 0 (persistidos 0, no persistidos 0)
- DLQ: {"checked": true, "scanned": 0}, mensajes de la ejecución: 0
- Espera: 2.6 s (máx. 180.0), lag final 0
- Tiempo hasta persistencia (ms): {"p50": 624.398, "p95": 1815.481, "p99": 37501.794}
- Discrepancias: 0
- **Resultado: OK**

