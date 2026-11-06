# TDAgentico
Telemetría y Desarrollo Agéntico Extremo

MVP de monitoreo de flotas. **Etapa 1: ingesta de posiciones.**

```
POST /v1/positions ──► FastAPI ──(acks=all)──► Kafka: fleet.positions.v1 ──► consumidor ──(1 transacción)──► TimescaleDB
                                                     └─ inválidos ──► fleet.positions.dlq
```

## Estructura

| Ruta | Contenido |
|---|---|
| `contracts/fleet_contracts/` | Contrato del evento (Pydantic), compartido por API, consumidor y pruebas |
| `services/api/` | API FastAPI con productor `aiokafka` |
| `services/consumer/` | Consumidor `confluent-kafka` + `psycopg` |
| `db/migrations/001_init.sql` | Esquema: `ingested_events`, `position_history` (hypertable), `vehicle_current_position` |
| `tests/acceptance/` | Pruebas de aceptación contra el stack real |

## Contrato del evento

```json
{
  "schema_version": 1,
  "event_id": "0192f5a8-7c1e-7b3a-9d2f-4e8a1c3b5d70",
  "vehicle_id": "TRK-0042",
  "captured_at": "2026-11-05T14:03:22.123456Z",
  "lat": -33.44889,
  "lon": -70.669265,
  "speed_kmh": 42.5
}
```

- El `event_id` lo genera el emisor. Un reintento debe reutilizarlo.
- Respuestas: `202` cuando Kafka confirmó el mensaje, `422` si el evento es inválido y `503` si Kafka no confirmó. En ese caso hay que reintentar con el mismo `event_id`.

## Garantías

- **Deduplicación:** el `event_id` se reclama en `ingested_events` (PK) dentro de la misma transacción que escribe el historial y la posición actual. Si ya existía, no se toca nada más.
- **Eventos atrasados:** siempre entran al historial. La posición visible solo cambia si `(captured_at, event_id)` es mayor que la guardada, así que un empate de fecha lo gana el `event_id` mayor.
- **Mismo `event_id` con payload distinto:** se conserva el primero y se registra un warning.
- **Offsets:** el commit es manual y síncrono, y se hace solo después del `COMMIT` en la base de datos. Si el consumidor cae entre ambos pasos, relee el lote y la deduplicación lo descarta.

## Ejecutar localmente

```bash
docker compose up -d --build
```

```bash
curl -X POST http://localhost:8000/v1/positions -H "Content-Type: application/json" -d '{"event_id":"0192f5a8-7c1e-7b3a-9d2f-4e8a1c3b5d70","vehicle_id":"TRK-0042","captured_at":"2026-11-05T14:03:22Z","lat":-33.44889,"lon":-70.669265,"speed_kmh":42.5}'
```

```bash
docker compose exec timescaledb psql -U fleet -d fleet -c "SELECT * FROM vehicle_current_position;"
```

## Pruebas de aceptación

Con el stack levantado, la forma más simple es correrlas dentro de Docker (no hace falta Python en el host):

```bash
docker compose --profile test run --rm tests -v
```

Dentro del contenedor AT-4 se omite, porque necesita la CLI de Docker. Para incluirla, corre pytest desde el host:

```bash
pip install -r tests/acceptance/requirements.txt
```

```bash
pytest -v
```

La primera vez que arranca, TimescaleDB puede tardar unos minutos en inicializarse en Docker Desktop para Windows.

- AT-1: ingesta normal.
- AT-2: reenvío del mismo evento, incluido un `event_id` repetido con payload distinto.
- AT-3: posición antigua después de una reciente, y empate de `captured_at`.
- AT-4 (`-m crash`): el consumidor cae entre el commit en la base y el commit de offset. Usa `docker compose stop/run/start` sobre el consumidor.

Para reiniciar la base desde cero (borra los datos):

```bash
docker compose down -v
```
