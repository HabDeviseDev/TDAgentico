# TDAgentico
Telemetría y Desarrollo Agéntico Extremo

MVP de monitoreo de flotas.

- **Etapa 1:** ingesta de posiciones.
- **Etapa 2:** portal en tiempo real con la última posición de cada vehículo.
- **Etapa 3:** zonas críticas y alertas de detención dentro de ellas.

```
POST /v1/positions ──► FastAPI ──(acks=all)──► Kafka: fleet.positions.v1 ──► consumidor ──(1 transacción)──► TimescaleDB
                                                     └─ inválidos ──► fleet.positions.dlq                          │
                                                                                                                    │ lectura periódica
Navegador ◄── SSE /v1/vehicles/stream ◄── nginx (portal) ◄── FastAPI: hub de snapshots (1 por instancia) ◄──────────┤
                                                              └─ hub de alertas: zonas + historial (1 por instancia) ◄─┘
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

Escenarios de demostración de alertas (Ctrl+C para terminar; ver "Demostración de alertas"):

```bash
docker compose --profile demo run --rm demo-alerts
```

## Estructura

| Ruta | Contenido |
|---|---|
| `contracts/fleet_contracts/` | Contrato del evento (Pydantic), compartido por API, consumidor y pruebas |
| `services/api/app/ingest.py` | Ruta de ingesta con productor `aiokafka` |
| `services/api/app/vehicles/` | Consulta de vehículos: `repository.py` (SQL), `snapshots.py` (hub compartido), `sse.py` (formato y stream por cliente), `routes.py` (HTTP) |
| `services/api/app/alerts/` | Zonas y alertas: `rules.py` (regla de detención, dominio puro), `repository.py` (SQL en una vista consistente), `hub.py` (evaluación compartida), `routes.py` (HTTP), `schemas.py` |
| `services/api/app/timed_read.py` | Timeout de lectura compartido (conexión + consulta, cancelación y descarte) |
| `services/api/app/db.py`, `settings.py` | Pool async de `psycopg` y configuración por variables de entorno |
| `services/portal/` | SPA React + TypeScript (Vite, Leaflet) servida por nginx con proxy a la API |
| `scripts/simulate.py` | Simulador de flota alrededor de Bogotá (solo biblioteca estándar) |
| `scripts/demo_alerts.py` | Escenarios de demostración de alertas (solo biblioteca estándar) |
| `services/consumer/` | Consumidor `confluent-kafka` + `psycopg` |
| `db/migrations/` | `001_init.sql` (deduplicación, historial hypertable, posición actual), `002_event_id_conflicts.sql` y `003_critical_zones.sql` (zonas y zonas de demostración) |
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
| `DB_READ_TIMEOUT_S` | api | `3` | Límite total de una lectura de posiciones (obtener la conexión + ejecutar la consulta), en `GET /v1/vehicles` y en el hub SSE |
| `PORTAL_STALE_AFTER_SECONDS` | portal | `120` | Antigüedad de `captured_at` a partir de la cual una posición se marca como desactualizada |
| `PORTAL_PORT` | portal | `8088` | Puerto del portal en el host |

Las variables con valor por defecto en `docker-compose.yml` se pueden cambiar al arrancar, sin reconstruir:

```bash
VEHICLES_POLL_INTERVAL_S=2 PORTAL_STALE_AFTER_SECONDS=60 docker compose up -d api portal
```

El umbral de antigüedad llega al navegador por `/config.json`, que nginx genera al arrancar con las variables de entorno.

### Timeout de lectura

Cuando una lectura supera `DB_READ_TIMEOUT_S` (BD lenta, tabla bloqueada, pool agotado):

- `GET /v1/vehicles` responde `503` con `"La base de datos no respondió a tiempo"`.
- El hub envía `status: degraded` con ese mismo detalle y conserva el último snapshot. Los clientes que conecten mientras tanto reciben el estado degradado y ese snapshot.
- La consulta pendiente se cancela: se pide al servidor que la cancele (`cancel_safe`), se cierra la conexión y el pool la descarta y la repone.
- El siguiente ciclo del hub vuelve a intentar la lectura. Al lograrlo envía `status: ok` y un snapshot completo.
- Como red de seguridad, las conexiones de la API usan un `statement_timeout` de `DB_READ_TIMEOUT_S + 2` segundos en el servidor y se identifican como `application_name = fleet-api`.

Cada ciclo del hub dura como máximo `DB_READ_TIMEOUT_S` más `VEHICLES_POLL_INTERVAL_S`. Con los valores por defecto, los clientes ven `degraded` unos 3 a 4 s después de que la BD deja de responder.

Para comprobarlo a mano con una lectura que queda esperando indefinidamente, bloquea la tabla en una sesión de `psql` y mantenla abierta:

```bash
docker compose exec timescaledb psql -U fleet -d fleet
```

Dentro de `psql`:

```sql
BEGIN;
LOCK TABLE vehicle_current_position IN ACCESS EXCLUSIVE MODE;
```

Mientras tanto, `GET /v1/vehicles` responde `503` a los 3 s y el portal muestra "BD sin respuesta". Al ejecutar `ROLLBACK;` se recupera en el siguiente ciclo.

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
- **Velocidad** describe solo la última lectura. La duración de una detención la calcula la evaluación de alertas a partir del historial (ver abajo).

### Proxy (nginx)

- `/v1/vehicles/stream` usa `proxy_buffering off`, `gzip off`, HTTP/1.1 sin `Connection: close` y `proxy_read_timeout` de 120 s, ocho veces el heartbeat. La API además envía `X-Accel-Buffering: no`.
- `/v1/` reenvía el resto de la API, incluida la ingesta `POST /v1/positions`.

## Zonas críticas y alertas de detención

### Zonas

Tabla `critical_zones`: `zone_id`, `name`, `center_lat`, `center_lon`, `radius_m`, `active` e `is_demo`. Son círculos; la distancia al centro se calcula en metros con la fórmula del semiverseno (haversine, radio medio de la Tierra 6 371 008,8 m). Un punto está dentro si su distancia es **menor o igual** al radio.

La migración `003_critical_zones.sql` crea la tabla e inserta **zonas de demostración ficticias** alrededor de Bogotá. No representan zonas reales de riesgo. En el portal se dibujan con borde punteado, se llaman "Demo · …" y la leyenda dice "Zonas de demostración (ficticias)".

| `zone_id` | Nombre | Radio | Activa |
|---|---|---|---|
| `DEMO-Z01` | Demo · Centro logístico Puente Aranda | 400 m | sí |
| `DEMO-Z02` | Demo · Terminal Salitre | 350 m | sí |
| `DEMO-Z03` | Demo · Corredor Avenida La Esperanza (se superpone con Z02) | 300 m | sí |
| `DEMO-Z04` | Demo · Patio Usaquén | 250 m | sí |
| `DEMO-Z05` | Demo · Zona inactiva Kennedy | 300 m | no |

La migración es idempotente (`ON CONFLICT DO NOTHING`): no pisa cambios posteriores, como desactivar una zona.

**`GET /v1/zones`**: zonas activas.

```json
{"generated_at": "…Z", "count": 4, "zones": [
  {"zone_id": "DEMO-Z01", "name": "Demo · Centro logístico Puente Aranda",
   "center_lat": 4.623, "center_lon": -74.115, "radius_m": 400.0, "is_demo": true}]}
```

### Regla de detención

Está en `services/api/app/alerts/rules.py`: funciones puras y deterministas que reciben las muestras, las zonas, los parámetros y la hora de evaluación (reloj inyectable). No acceden a la BD ni al reloj.

- **Orden:** por `captured_at`. Con timestamps iguales se usa una sola muestra, la de mayor `event_id`: el mismo desempate de `vehicle_current_position`. Las muestras duplicadas cuentan una vez.
- **Muestra de detención:** velocidad ≤ `ALERT_MAX_SPEED_KMH`.
- **Tramo** dentro de una zona: muestras consecutivas que
  - son de detención,
  - están dentro de la misma zona,
  - están separadas de la anterior por ≤ `ALERT_MAX_GAP_S`, y
  - están a ≤ `ALERT_MAX_DISPLACEMENT_M` de la **primera** muestra del tramo.
- **Rupturas:**
  - Una muestra de movimiento, una fuera de la zona o una separación mayor rompen el tramo.
  - Una muestra que supera el desplazamiento rompe el tramo anterior y empieza uno nuevo, porque sigue siendo una detención en la zona.
- **Continuidad inferida:** entre dos muestras no hay datos. Se supone que el vehículo siguió detenido entre ellas **solo** si la separación es ≤ `ALERT_MAX_GAP_S`.
- **Duración observada:** `captured_at` de la última muestra del tramo menos la de la primera. El paso del tiempo sin muestras nuevas **no** la aumenta.
- **Alerta:** el tramo actual (el que contiene la última muestra del vehículo) dura **más** de `ALERT_AFTER_S`. Con exactamente el umbral no hay alerta.
- **Zonas superpuestas:** cada zona se evalúa por separado. Un vehículo dentro de dos zonas puede tener una alerta por cada una.
- **Evidencia insuficiente:** si la última muestra tiene más de `ALERT_MAX_SAMPLE_AGE_S` respecto a la hora de evaluación, no se emite alerta y el vehículo aparece en `insufficient_evidence` con el motivo `stale_last_sample`. Se aplica tanto a vehículos detenidos como en movimiento.
- **Identificador de alerta:** `uuid5(vehículo | zona | event_id de la primera muestra del tramo)`. Es estable mientras dura la misma detención en la misma zona; una detención nueva tiene otro.
- **Ventana histórica** (`ALERT_WINDOW_S`, 2 h): solo se leen muestras con `captured_at` dentro de la ventana. Si el tramo empieza en la primera muestra disponible y esa muestra está a ≤ `ALERT_MAX_GAP_S` del límite, una muestra anterior podría continuarlo. En ese caso `start_unknown: true`, la duración es un **mínimo observado** y el identificador usa un marcador fijo en lugar de la primera muestra, porque esta cambia al avanzar la ventana.

### Eventos atrasados

Cada evaluación lee de nuevo el historial confirmado, incluidos los eventos atrasados que se hayan persistido antes de ella. Un evento atrasado que muestra movimiento, o que deja una separación mayor, rompe el tramo y la siguiente evaluación retira la alerta. Uno que rellena una interrupción puede crearla. No hace falta que cambie la posición actual.

### `GET /v1/alerts`

Evalúa en la propia petición a partir de los datos persistidos. Responde `503` si la BD no responde o si se supera `DB_READ_TIMEOUT_S`.

```json
{
  "evaluated_at": "2026-11-06T19:49:59.198748Z",
  "window_start": "2026-11-06T17:49:59.198748Z",
  "parameters": {"max_speed_kmh": 3.0, "max_displacement_m": 50.0, "max_gap_s": 120.0,
                 "max_sample_age_s": 120.0, "alert_after_s": 1200.0, "window_s": 7200.0,
                 "max_vehicles": 1000, "max_rows": 100000},
  "complete": true,
  "incomplete_reasons": [],
  "coverage": {"vehicles_in_window": 20, "vehicles_evaluated": 20,
               "vehicles_not_evaluated": 0, "vehicles_without_recent_samples": 112},
  "zone_ids": ["DEMO-Z01", "DEMO-Z02", "DEMO-Z03", "DEMO-Z04"],
  "alert_count": 1,
  "alerts": [{
    "alert_id": "365b1523-79fb-50f9-9036-6d5c2237683f",
    "vehicle_id": "DEMO-1940-ZONA25", "zone_id": "DEMO-Z01",
    "zone_name": "Demo · Centro logístico Puente Aranda",
    "started_at": "2026-11-06T19:15:58.000000Z",
    "last_sample_at": "2026-11-06T19:49:42.175837Z",
    "observed_duration_s": 2024.175837, "start_unknown": false,
    "sample_count": 69, "lat": 4.624324, "lon": -74.114989}],
  "insufficient_evidence_count": 1,
  "insufficient_evidence": [{
    "vehicle_id": "DEMO-1935-ATRASADO", "reason": "stale_last_sample",
    "detail": "La última muestra supera la antigüedad máxima de 120 s: no se puede confirmar su estado actual",
    "last_sample_at": "2026-11-06T19:35:07.000000Z"}]
}
```

- `complete: false` cuando se alcanzó `ALERTS_MAX_VEHICLES` o `ALERTS_MAX_ROWS`. `incomplete_reasons` dice cuál (`max_vehicles`, `max_rows`) y `coverage` cuántos vehículos quedaron sin evaluar.
  - Con el tope de vehículos se evalúan los primeros por `vehicle_id`.
  - Con el tope de filas, el vehículo cuyo historial quedó cortado se descarta entero: no se evalúa con datos parciales.
- `vehicles_without_recent_samples`: vehículos con posición actual pero sin muestras en la ventana. No pueden tener alertas y no se listan uno por uno.

### Vista consistente, timeout y costo

- **Vista consistente:** zonas, conteos e historial se leen en **una** transacción `REPEATABLE READ, READ ONLY`, con tres consultas en total y nunca una por vehículo. Todas ven la misma instantánea aunque el consumidor siga insertando.
- **Timeout y pool:** se reutilizan el pool y el mismo `DB_READ_TIMEOUT_S` (`app/timed_read.py`). Al vencer, la consulta se cancela en el servidor y la conexión se descarta.
- **Costo:** O(muestras en la ventana) por evaluación. Con 2 h y una muestra cada 2 s son 3600 filas por vehículo: 1000 vehículos serían 3,6 M filas, por encima del tope por defecto (100 000), y el resultado se marcaría parcial.
  - La regla en Python solo recorre hacia atrás desde la última muestra mientras se cumplen velocidad, zona y continuidad, así que los vehículos en movimiento cuestan O(1).
  - La evaluación corre en un hilo para no frenar el SSE.
- **Índices:**
  - La hypertable `position_history` está particionada por `captured_at` (chunks de 1 día), con el índice `position_history_captured_at_idx` y la PK `(vehicle_id, captured_at, event_id)`.
  - El filtro de ventana usa exclusión de chunks y el índice de `captured_at`. El orden `(vehicle_id, captured_at, event_id DESC)` se resuelve con un sort en memoria de las filas de la ventana (unos 70 kB para 374 filas en la prueba).
  - `EXPLAIN ANALYZE` con los datos de demostración: dos `Index Scan` sobre `captured_at` y 1,6 ms.
  - No hizo falta un índice nuevo. Los vehículos evaluados se acotan con `vehicle_id <= último id` en lugar de un `JOIN`: con la ventana vacía el planificador elegía un bucle anidado que recorría la ventana una vez por vehículo.
- **Para escalar:** evaluación incremental (estado del tramo por vehículo, actualizado por el consumidor al persistir), particionar la evaluación por grupo de vehículos o reducir la ventana. Una tabla de historial de alertas permitiría además auditar aperturas y cierres. En este incremento las alertas son resultados derivados.

### Tiempo real

El stream existente `/v1/vehicles/stream` transmite también las alertas. Cada instancia de API tiene un **hub de alertas** que evalúa cada `ALERTS_EVAL_INTERVAL_S`, sin importar cuántos clientes haya.

| Evento | Cuándo | Datos |
|---|---|---|
| `alerts_status` | Al conectar y cuando cambia la salud de la evaluación | `{"type":"alerts_status","status":"ok"\|"degraded","generated_at","detail","last_evaluation_at"}` |
| `alerts` | Al conectar, cuando cambia el resultado y al recuperarse de un fallo | Igual que `GET /v1/alerts`, más `type` y `sequence` |

- **Detección de cambios:** el hub reevalúa aunque la posición actual no cambie. Un evento atrasado o una última muestra que se desactualiza producen un resultado distinto y se difunden. Se compara el resultado sin la hora de evaluación, así que un resultado idéntico no se reenvía.
- **Si la evaluación falla:**
  - Se envía `alerts_status: degraded` con `last_evaluation_at`, la última evaluación correcta.
  - Se conserva el último resultado y el siguiente ciclo reintenta.
  - Nunca se publica una lista vacía en lugar de un resultado que no se pudo calcular.
  - Al recuperarse se envían `ok` y el resultado completo.
- **Memoria acotada:** cada cliente tiene un solo buzón, compartido por los dos hubs, con como máximo un evento pendiente por tipo (`status`, `snapshot`, `alerts_status` y `alerts`). La suscripción a ambos hubs se libera al desconectar.

### En el portal

- **Mapa:**
  - Zonas como círculos con nombre.
  - Vehículos con alerta en rojo.
  - Leyenda que identifica las zonas de demostración.
- **Panel "Alertas activas":**
  - Cantidad de alertas.
  - Por alerta: vehículo, zona, duración observada (con "≥" e "Inicio desconocido" si corresponde) y fecha y antigüedad de la última muestra.
  - Al hacer clic se selecciona el vehículo.
  - Lista plegable de evidencia insuficiente.
  - Resumen de la regla aplicada.
- **Indicadores:**
  - "Evaluando alertas…" antes del primer resultado.
  - Error si falla sin resultado previo (no muestra "0 alertas").
  - "Resultado desactualizado" con la última evaluación correcta si falla después.
  - "Evaluación parcial" con los vehículos evaluados frente al total.
  - Conteo "(desactualizado)" en la barra superior.
- Cada resultado `alerts` reemplaza al anterior. Una alerta que deja de cumplirse desaparece en el siguiente resultado correcto.
- "Sin cambios desde …" es la hora de la evaluación que produjo el resultado visible; las evaluaciones posteriores con el mismo resultado no se retransmiten.
- El detalle del vehículo seleccionado muestra su alerta o su evidencia insuficiente. Se conservan el buscador, la selección y los indicadores anteriores.

### Configuración

| Variable | Por defecto | Efecto |
|---|---|---|
| `ALERT_MAX_SPEED_KMH` | `3` | Velocidad máxima de una muestra de detención |
| `ALERT_MAX_DISPLACEMENT_M` | `50` | Distancia máxima a la primera muestra del tramo |
| `ALERT_MAX_GAP_S` | `120` | Separación máxima entre muestras consecutivas |
| `ALERT_MAX_SAMPLE_AGE_S` | `120` | Antigüedad máxima de la última muestra; más vieja = evidencia insuficiente |
| `ALERT_AFTER_S` | `1200` | Alerta si la duración observada es mayor (20 min) |
| `ALERT_WINDOW_S` | `7200` | Ventana de historial evaluada (2 h) |
| `ALERTS_MAX_VEHICLES` | `1000` | Vehículos por evaluación; más = resultado parcial |
| `ALERTS_MAX_ROWS` | `100000` | Filas de historial por evaluación; más = resultado parcial |
| `ALERTS_EVAL_INTERVAL_S` | `5` | Cada cuánto el hub reevalúa |

Todas son del servicio `api` y se pueden cambiar al arrancar:

```bash
ALERT_AFTER_S=300 docker compose up -d api
```

Para ver a mano la evaluación degradada, bloquea el historial en una sesión de `psql` (`BEGIN; LOCK TABLE position_history IN ACCESS EXCLUSIVE MODE;`). `GET /v1/alerts` responde `503` y el panel muestra "Resultado desactualizado"; `ROLLBACK;` lo recupera. Mientras dure el bloqueo, el consumidor también queda esperando.

## Demostración de alertas

`scripts/demo_alerts.py` publica **datos ficticios de demostración** por la API existente, con fechas relativas al momento de ejecutarlo. Los vehículos se llaman `DEMO-<HHMM>-<escenario>`. La etiqueta (`--tag`, por defecto la hora UTC) evita que una ejecución anterior interfiera con la nueva.

| Vehículo | Escenario | Resultado esperado |
|---|---|---|
| `…-ZONA25` | Detenido en `DEMO-Z01` 25 min, muestras cada 30 s hasta el presente | Alerta |
| `…-FUERA` | Detenido 30 min fuera de las zonas | Sin alerta |
| `…-MOVIL` | En movimiento (15–25 km/h) dentro de `DEMO-Z01` | Sin alerta |
| `…-HUECO` | Detenido en `DEMO-Z01` 40 min con 6 min sin muestras | Sin alerta (el tramo se reinicia) |
| `…-SINDATOS` | Última muestra hace 6 min | Evidencia insuficiente |
| `…-ATRASADO` | Detenido 30 min en `DEMO-Z04`; tras ver la alerta se publica un movimiento de hace 12 min | Alerta y luego se retira |

El script espera la alerta de `ATRASADO`, publica el evento atrasado, espera a que se retire y muestra el estado de cada vehículo según `GET /v1/alerts`. Después sigue en vivo y envía una muestra actual cada 30 s, para que las últimas muestras no se desactualicen. En vivo, `HUECO` y `ATRASADO` acumulan un tramo nuevo y generan alerta unos 12 y 8 min después: es el comportamiento esperado.

```bash
docker compose --profile demo run --rm demo-alerts
```

Desde el host (solo biblioteca estándar):

```bash
python scripts/demo_alerts.py --url http://localhost:8000
```

Opciones: `--once` publica y termina; `--live-duration` limita el tiempo en vivo en segundos; `--tag` fija la etiqueta. Los reintentos reutilizan el mismo `event_id`.

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
| `tests/unit/test_read_timeout.py` | Controlada (pool falso que se cuelga) | Consulta y adquisición colgadas superan el timeout; la conexión se cancela, se devuelve y se descarta; el hub degrada, conserva el snapshot y se recupera; `GET` responde `503` |
| `tests/acceptance/test_read_timeout_lock.py` | Stack real | `LOCK TABLE` exclusivo: `GET` responde `503` al vencer el timeout, el SSE pasa a `degraded`, las lecturas canceladas no se acumulan en `pg_stat_activity` y al liberar llegan `ok` y un snapshot |
| `tests/acceptance/test_vehicles_realtime.py` | Stack real (+ portal) | Última posición por REST, snapshot inicial, actualización tras persistir, evento atrasado, snapshot completo tras reconectar, SSE sin buffering a través de nginx |
| `tests/unit/test_stop_rule.py` | Dominio puro (reloj inyectado) | Más de 20 min y exactamente 20; frontera del radio; fuera de zona; salida de zona; zonas superpuestas; movimiento; velocidad igual al umbral; desplazamiento (49,99 m frente a 50+); interrupción (120 frente a 121 s); última muestra desactualizada; duplicados y empates de timestamp; eventos atrasados que retiran o crean la alerta; inicio truncado por la ventana; identificador estable; límites de vehículos y filas → evaluación parcial |
| `tests/unit/test_alert_hub.py` | Controlada (BD falsa y reloj) | Transacción `REPEATABLE READ` con tres consultas; timeout con consulta colgada y descarte de la conexión; resultado completo al conectar; cambios sin cambiar la posición actual (evento atrasado y antigüedad); `degraded` que conserva el resultado y recuperación; fallo sin resultado previo sin lista vacía; resultado parcial por límites; stream con ambos hubs y liberación; cliente lento acotado; `GET /v1/alerts` 200/503 y `GET /v1/zones` |
| `tests/acceptance/test_alerts.py` | Stack real | Zonas activas de demostración (la inactiva no aparece); alerta tras 25 min detenido en una zona de prueba, retirada por un evento atrasado en REST y SSE sin cambiar la posición actual; `LOCK TABLE position_history`: `503`, `alerts_status: degraded` y recuperación |

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

## Limitaciones de la etapa 3

- **Alertas derivadas:** se calculan en cada evaluación, sin tabla de historial de alertas. No se registra cuándo se abrió o se cerró una alerta.
- **Evaluación por instancia:** con varias instancias de API, cada una evalúa por su cuenta (mismo resultado, costo multiplicado).
- **Costo O(muestras en la ventana):** adecuado para la demostración. Para flotas grandes hace falta la evaluación incremental descrita arriba.
- **Inicio desconocido:** si una detención supera la ventana, su identificador cambia una vez, porque pasa a usar el marcador fijo. Dos detenciones distintas del mismo vehículo y zona que lleguen al límite de la ventana compartirían ese identificador, aunque no pueden coexistir.
- **Muestras con `captured_at` futuro:** la API acepta hasta 5 min de desfase. Esas muestras cuentan como vigentes (antigüedad 0) hasta que pase su hora.
- **Datos de demostración:** cada ejecución del script deja vehículos `DEMO-…` en la BD. Al dejar de recibir muestras pasan a evidencia insuficiente y salen de la evaluación cuando su última muestra queda fuera de la ventana de 2 h.

## Reiniciar desde cero

Borra los datos de TimescaleDB y de Kafka:

```bash
docker compose down -v
```
