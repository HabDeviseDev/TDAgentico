# TDAgentico
Telemetría y Desarrollo Agéntico Extremo

MVP de monitoreo de flotas.

- **Etapa 1:** ingesta de posiciones.
- **Etapa 2:** portal en tiempo real con la última posición de cada vehículo.

```
POST /v1/positions ──► FastAPI ──(acks=all)──► Kafka: fleet.positions.v1 ──► consumidor ──(1 transacción)──► TimescaleDB
                                                     └─ inválidos ──► fleet.positions.dlq                          │
                                                                                                                    │ lectura periódica
Navegador ◄── SSE /v1/vehicles/stream ◄── nginx (portal) ◄── FastAPI: hub de snapshots (1 por instancia) ◄──────────┘
```

## Inicio rápido

```bash
docker compose up -d --build --wait
```

```bash
docker compose --profile demo up -d simulator
```

Abre el portal en **http://localhost:8088**. Para detener el simulador de forma limpia:

```bash
docker compose --profile demo stop simulator
```

## Estructura

| Ruta | Contenido |
|---|---|
| `contracts/fleet_contracts/` | Contrato del evento (Pydantic), compartido por API, consumidor y pruebas |
| `services/api/app/ingest.py` | Ruta de ingesta con productor `aiokafka` |
| `services/api/app/vehicles/` | Consulta de vehículos: `repository.py` (SQL), `snapshots.py` (hub compartido), `sse.py` (formato y stream por cliente), `routes.py` (HTTP) |
| `services/api/app/db.py`, `settings.py` | Pool async de `psycopg` y configuración por variables de entorno |
| `services/portal/` | SPA React + TypeScript (Vite, Leaflet) servida por nginx con proxy a la API |
| `scripts/simulate.py` | Simulador de flota alrededor de Bogotá (solo biblioteca estándar) |
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

## Portal en tiempo real

| URL | Qué es |
|---|---|
| http://localhost:8088 | Portal (puerto configurable con `PORTAL_PORT`) |
| http://localhost:8088/v1/... | API a través del proxy del portal (mismo origen para REST, SSE e ingesta) |
| http://localhost:8000/docs | API directa (OpenAPI) |

El portal usa el puerto 8088 por defecto porque en el equipo donde se desarrolló el 8080 lo ocupa el listener de Oracle. Para cambiarlo:

```bash
PORTAL_PORT=9090 docker compose up -d portal
```

### Endpoints nuevos

**`GET /v1/vehicles`**: posición actual de cada vehículo, leída de `vehicle_current_position`. Responde `503` si la BD no responde.

```json
{
  "generated_at": "2026-11-06T06:18:23.512000Z",
  "count": 1,
  "vehicles": [{
    "vehicle_id": "BOG-001", "event_id": "68ba91eb-…", "captured_at": "2026-11-06T06:18:22.000000Z",
    "lat": 4.60935, "lon": -74.077612, "speed_kmh": 38.7, "updated_at": "2026-11-06T06:18:22.410000Z"
  }]
}
```

**`GET /v1/vehicles/stream`**: Server-Sent Events.

| Evento | Cuándo | Datos |
|---|---|---|
| `status` | Al conectar y cuando cambia la salud de la lectura de la BD | `{"type":"status","status":"ok"\|"degraded","generated_at","detail","last_snapshot_at"}` |
| `snapshot` | Al conectar y cada vez que cambia el estado visible | Igual que `GET /v1/vehicles`, más `type` y `sequence` (con `id:` SSE) |
| `heartbeat` | Tras `SSE_HEARTBEAT_S` sin otros eventos | `{"type":"heartbeat","generated_at"}` |

Todas las fechas del contrato están en UTC con sufijo `Z`. El portal las muestra en la zona horaria del navegador.

### Qué se transmite

- Se transmite el **estado actual** (la última posición por vehículo), siempre como snapshot completo. No se envían deltas: tras una reconexión, el primer snapshot basta para quedar al día.
- Si un vehículo envía varias posiciones entre dos lecturas, el portal solo ve la última. Las posiciones intermedias siguen disponibles en `position_history`.
- Los snapshots se leen de la BD con `autocommit`, así que solo incluyen datos confirmados. Un dato llega al portal después del `COMMIT` del consumidor, con como máximo un intervalo de lectura de retraso.

### Configuración

| Variable | Servicio | Por defecto | Efecto |
|---|---|---|---|
| `VEHICLES_POLL_INTERVAL_S` | api | `1` | Cada cuánto el hub lee `vehicle_current_position` |
| `SSE_HEARTBEAT_S` | api | `15` | Silencio máximo antes de enviar un heartbeat |
| `SSE_MAX_CLIENTS` | api | `500` | Conexiones SSE por instancia (luego responde `503`) |
| `VEHICLES_MAX` | api | `10000` | Tope de filas por lectura |
| `DB_POOL_MAX` | api | `5` | Conexiones máximas del pool |
| `PORTAL_STALE_AFTER_SECONDS` | portal | `120` | Antigüedad de `captured_at` a partir de la cual una posición se marca como desactualizada |
| `PORTAL_PORT` | portal | `8088` | Puerto del portal en el host |

Las variables con valor por defecto en `docker-compose.yml` se pueden cambiar al arrancar, sin reconstruir:

```bash
VEHICLES_POLL_INTERVAL_S=2 PORTAL_STALE_AFTER_SECONDS=60 docker compose up -d api portal
```

El umbral de antigüedad llega al navegador por `/config.json`, que nginx genera al arrancar con las variables de entorno.

### Costo de la lectura periódica y cómo escalar

- Cada instancia de API hace **una** consulta por intervalo, sin importar cuántos navegadores estén conectados. El resultado se serializa una vez y se reparte a todos.
- El costo es O(N vehículos) por intervalo: lectura completa de la tabla, comparación con la anterior y, si cambió, un snapshot de unos 200 bytes por vehículo. Con 1 s de intervalo y cientos de vehículos es despreciable.
- Para miles de vehículos:
  1. Reemplazar el sondeo por una notificación después del `COMMIT` (`LISTEN/NOTIFY` emitido por el consumidor, o un tópico Kafka de cambios confirmados).
  2. Enviar deltas con número de secuencia y un snapshot solo al conectar o ante un hueco.
  3. Filtrar por área visible o por grupo de vehículos.
  4. Comprimir o paginar el snapshot inicial.

  Con varias instancias de API, cada una mantiene su propio hub.

### Comportamiento del portal

- El SSE es la única fuente de datos: no hay una carga REST previa que pueda competir con el tiempo real. Cada snapshot reemplaza la lista completa y la selección se conserva si el vehículo sigue existiendo.
- La **conexión** (verde "En tiempo real" / rojo "Conexión perdida") es independiente de la **salud de los datos** ("al día" / "BD sin respuesta"). Si la BD falla, el SSE sigue abierto y el portal muestra el estado degradado. Si se pierde la conexión, conserva los últimos datos y lo indica. Si no llega nada durante 45 s, ni siquiera un heartbeat, reconecta.
- **Desactualizado** se calcula con `captured_at` frente al reloj del navegador, no con la hora de llegada.
- **Velocidad 0** describe solo la última lectura. La duración de una detención se implementará después, a partir del historial.

### Proxy (nginx)

- `/v1/vehicles/stream` usa `proxy_buffering off`, `gzip off`, HTTP/1.1 sin `Connection: close` y `proxy_read_timeout` de 120 s, ocho veces el heartbeat. La API además envía `X-Accel-Buffering: no`.
- `/v1/` reenvía el resto de la API, incluida la ingesta `POST /v1/positions`.

## Simulador

Envía posiciones por la API existente, como lo haría un dispositivo. Usa solo la biblioteca estándar de Python, así que también corre en el host:

```bash
python scripts/simulate.py --url http://localhost:8000 --vehicles 8 --interval 2 --duration 300
```

| Opción | Por defecto | Descripción |
|---|---|---|
| `--url` | `http://localhost:8000` | URL base de la API (también sirve `http://localhost:8088`) |
| `--vehicles` | `8` | Vehículos `BOG-001`, `BOG-002`, … (identificadores estables) |
| `--interval` | `2` | Segundos entre posiciones de cada vehículo |
| `--duration` | `0` | Segundos de simulación (`0` = hasta Ctrl+C) |
| `--seed` | `42` | Semilla: los mismos recorridos en cada ejecución |
| `--retries` | `3` | Reintentos por posición; reutilizan el mismo `event_id` |

- Cada posición nueva lleva un `event_id` nuevo y `captured_at` con la hora actual en UTC.
- Los recorridos son circuitos alrededor de barrios de Bogotá, con paradas ocasionales a velocidad 0.
- Ctrl+C o `SIGTERM` terminan el ciclo en curso y muestran un resumen.

Con Docker, `SIM_VEHICLES`, `SIM_INTERVAL` y `SIM_DURATION` configuran el servicio `simulator`:

```bash
SIM_VEHICLES=20 SIM_INTERVAL=1 docker compose --profile demo up -d simulator
```

## Ingesta manual

```bash
curl -X POST http://localhost:8000/v1/positions -H "Content-Type: application/json" -d '{"event_id":"0192f5a8-7c1e-7b3a-9d2f-4e8a1c3b5d70","vehicle_id":"TRK-0042","captured_at":"2026-11-05T14:03:22Z","lat":4.6486,"lon":-74.0628,"speed_kmh":42.5}'
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
| `tests/unit/test_snapshot_hub.py` | Controlada (BD falsa) | Snapshot inicial, una lectura compartida, solo emite si cambia, BD caída → `degraded` y recuperación, cliente lento con memoria acotada, heartbeat, liberación al desconectar, `GET /v1/vehicles` 200/503 |
| `tests/unit/test_simulator.py` | Controlada (servidor HTTP local) | Reintento con el mismo `event_id`, IDs estables, `event_id` nuevos, UTC, recorridos reproducibles |
| `tests/acceptance/test_vehicles_realtime.py` | Stack real (+ portal) | Última posición por REST, snapshot inicial, actualización tras persistir, evento atrasado, snapshot completo tras reconectar, SSE sin buffering a través de nginx |

Pruebas del portal (tipos, build y Vitest):

```bash
docker build --target test services/portal
```

El error de BD con el stack real se puede ver con un cliente SSE abierto: `docker compose stop timescaledb` produce `status: degraded` y `docker compose start timescaledb` produce `status: ok` seguido de un snapshot.

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

## Limitaciones y decisiones de la etapa 2

- **Sondeo en vez de notificaciones:** más simple y correcto para el MVP, con hasta `VEHICLES_POLL_INTERVAL_S` de retraso. Ver la sección de escalado.
- **Snapshots completos:** simplifican la recuperación. Su tamaño crece con la flota.
- **Sin autenticación** en el portal ni en la API.
- **Datos de pruebas en el mapa:** las pruebas de aceptación dejan vehículos `TEST-…`, `AT4-…` y `PERSIST-…` en la BD. Los antiguos (anteriores a esta etapa) están en Santiago de Chile y quedan marcados como desactualizados. `docker compose down -v` los borra junto con todos los datos.
- **Teselas de OpenStreetMap** cargadas directamente desde `tile.openstreetmap.org`, solo para demostración y respetando su atribución. Para producción hay que usar un proveedor con acuerdo de uso.
- **Detención:** no se calcula su duración, solo se informa la velocidad de la última lectura.

## Reiniciar desde cero

Borra los datos de TimescaleDB y de Kafka:

```bash
docker compose down -v
```
