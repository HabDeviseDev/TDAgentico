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
| `db/migrations/` | `001_init.sql` (deduplicación, historial hypertable, posición actual) y `002_event_id_conflicts.sql` |
| `db/migrate.sh` | Aplica las migraciones pendientes y las registra en `schema_migrations` (servicio `migrate`) |
| `tests/unit/` | Pruebas controladas con dobles de Kafka y BD (no necesitan el stack) |
| `tests/acceptance/` | Pruebas contra el stack real |
| `docs/auditoria-ia.md` | Registro de decisiones e intervenciones de la IA |

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
- **Mismo `event_id` con payload distinto:** se conserva el original y el conflicto se guarda en `event_id_conflicts` (event_id, hash original, hash recibido, tópico, partición, offset y fecha), dentro de la misma transacción del lote. La clave primaria es `(tópico, partición, offset)`, así que reprocesar el mismo mensaje no duplica el registro.
- **Offsets:** el commit es manual y síncrono, y se hace solo después del `COMMIT` en la base de datos. Si el consumidor cae entre ambos pasos, relee el lote y la deduplicación lo descarta.
- **DLQ:** un mensaje inválido se publica en `fleet.positions.dlq` y su offset se confirma solo después de que Kafka confirma la DLQ. Si la DLQ falla, el consumidor termina sin confirmar ningún offset del lote y Docker lo reinicia para releerlo.
- **Persistencia de Kafka:** `KAFKA_LOG_DIRS=/var/lib/kafka/data` en el volumen `kafka-data`. Ahí viven los mensajes, los offsets de los grupos (`__consumer_offsets`) y los metadatos KRaft. `CLUSTER_ID` está fijo porque el volumen queda formateado con ese ID.

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

La primera vez que arranca, TimescaleDB puede tardar unos minutos en inicializarse en Docker Desktop para Windows.

## Migraciones

El servicio `migrate` corre en cada `docker compose up` antes del consumidor. Aplica en orden los archivos de `db/migrations/` que no estén en `schema_migrations`. Sirve igual con un volumen nuevo que con uno existente, porque ya no depende de `docker-entrypoint-initdb.d`, que solo corre con el volumen vacío.

Aplicar las migraciones pendientes sobre la base existente (sin borrar datos):

```bash
docker compose run --rm migrate
```

Ver qué migraciones están aplicadas:

```bash
docker compose exec timescaledb psql -U fleet -d fleet -c "SELECT * FROM schema_migrations ORDER BY version;"
```

## Pruebas

Con el stack levantado, se corren dentro de Docker (no hace falta Python en el host):

```bash
docker compose --profile test run --rm tests -v
```

Solo las controladas (no usan Kafka ni la BD):

```bash
docker compose --profile test run --rm --no-deps tests tests/unit -v
```

| Prueba | Tipo | Qué comprueba |
|---|---|---|
| `tests/unit/test_api_kafka_errors.py` | Controlada (productor falso) | `503` cuando Kafka rechaza o no confirma a tiempo; `422` sin publicar |
| `tests/unit/test_consumer_dlq.py` | Controlada (Kafka y BD falsos) | Orden DLQ confirmada → COMMIT → offset; si la DLQ falla (error o timeout) o la BD falla, no se confirma ningún offset |
| `tests/acceptance/test_event_id_conflicts.py` | Solo TimescaleDB + stack | Reproceso del mensaje conflictivo: original intacto y un único registro; conflicto vía API |
| `tests/acceptance/test_dlq.py` | Stack real | Mensaje inválido publicado directo en Kafka llega a la DLQ y su offset queda confirmado |
| `tests/acceptance/test_ingestion.py` | Stack real | AT-1 ingesta normal, AT-2 reenvío, AT-3 posición antigua y empate |
| `tests/acceptance/test_crash_recovery.py` | Stack real + CLI Docker | AT-4 caída entre COMMIT y offset (se omite dentro del contenedor) |

Para incluir AT-4, corre pytest desde el host:

```bash
pip install -r services/api/requirements.txt -r services/consumer/requirements.txt -r tests/acceptance/requirements.txt
```

```bash
pytest -v
```

Los fallos de la DLQ y la falta de ack de Kafka hacia la API se prueban solo de forma controlada, porque provocarlos con el stack real no es determinista. El `503` con el stack real se puede ver deteniendo Kafka (paso opcional, ver abajo).

## Verificar que Kafka conserva mensajes y offsets al recrearse

Ninguno de estos pasos usa `-v`, así que los volúmenes existentes se conservan.

1. Detener el consumidor para dejar mensajes pendientes:
   ```bash
   docker compose stop consumer
   ```
2. Enviar un evento:
   ```bash
   curl -X POST http://localhost:8000/v1/positions -H "Content-Type: application/json" -d '{"event_id":"6f1c2b9e-1d2a-4c3b-9e8f-7a6b5c4d3e2f","vehicle_id":"PERSIST-1","captured_at":"2026-11-05T10:00:00Z","lat":-33.4,"lon":-70.6,"speed_kmh":30}'
   ```
3. Anotar offsets y lag del grupo (la columna `LAG` debe sumar al menos 1):
   ```bash
   docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group fleet-ingestion
   ```
4. Eliminar y recrear el contenedor de Kafka (el volumen `kafka-data` se conserva):
   ```bash
   docker compose rm -sf kafka
   ```
   ```bash
   docker compose up -d kafka
   ```
5. Repetir el paso 3. `CURRENT-OFFSET`, `LOG-END-OFFSET` y `LAG` deben ser iguales a los anotados.
6. Arrancar el consumidor y comprobar que procesa el pendiente (`LAG` 0 y la fila en la BD):
   ```bash
   docker compose start consumer
   ```
   ```bash
   docker compose exec timescaledb psql -U fleet -d fleet -c "SELECT * FROM position_history WHERE vehicle_id = 'PERSIST-1';"
   ```

Al pasar de la versión sin volumen a esta, el primer arranque crea un Kafka vacío (los datos anteriores estaban dentro del contenedor). `kafka-init` vuelve a crear los tópicos.

## API sin Kafka (opcional, stack real)

```bash
docker compose stop kafka
```

Un `POST` debe responder `503` en unos 5 segundos. Después:

```bash
docker compose start kafka
```

## Reiniciar desde cero

Borra los datos de TimescaleDB y de Kafka:

```bash
docker compose down -v
```
