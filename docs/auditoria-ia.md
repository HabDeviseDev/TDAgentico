# Auditoría de IA

Registro de decisiones e intervenciones del asistente de IA (Claude Code) en este repositorio. Solo incluye lo que realmente ocurrió. Las decisiones del usuario se indican como tales.

## 2026-11-05: Etapa 1, ingesta (commit `968db25`)

| # | Tipo | Qué ocurrió |
|---|---|---|
| 1 | Decisión de la IA, aprobada por el usuario | Se propuso el contrato, el esquema y los límites de transacción antes de implementar. El usuario aceptó las cinco decisiones abiertas: `event_id` generado por el emisor, `202 Accepted`, conservar el primero ante un ID repetido con payload distinto, transacción por lote, `aiokafka` en la API y `confluent-kafka` en el consumidor. |
| 2 | Intervención | Se implementó el código sin poder ejecutarlo: Docker no estaba instalado y `pip` en el host fallaba con `CERTIFICATE_VERIFY_FAILED`. Solo se comprobó que compilaba. |
| 3 | Acción bloqueada | Después de que el usuario instaló Docker, la IA intentó instalar las dependencias de pruebas en el host con `pip --trusted-host`. El clasificador de permisos lo bloqueó por debilitar TLS. No se intentó otra forma de saltar la verificación. |
| 4 | Decisión de la IA | Como alternativa, las pruebas pasaron a ejecutarse en un contenedor (`tests`, perfil `test`), donde `pip` sí descarga con verificación SSL normal. |
| 5 | Corrección | TimescaleDB tardó más de 90 s en inicializarse y quedaba `unhealthy`. Se agregó `start_period: 300s` al healthcheck. |
| 6 | Verificación | 13 pruebas pasaron en el contenedor. AT-4 se omite ahí porque necesita la CLI de Docker, así que se ejecutó a mano con un script temporal fuera del repositorio. Pasó. |
| 7 | Decisión de la IA | El usuario pidió el commit. La IA lo hizo en la rama nueva `feat/ingesta-posiciones` y no en `main`, aunque antes había ofrecido hacerlo en `main`. |
| 8 | Intervención | El `push` falló por SSL con los certificados propios de Git. Se reintentó con `git -c http.sslBackend=schannel` solo para ese comando, sin cambiar la configuración. La verificación TLS siguió activa. Falló por falta de credenciales y la IA no introdujo ninguna. |
| 9 | Acción del usuario | El usuario hizo el `push` con su cuenta. La IA confirmó la rama remota con `git fetch`. El PR no lo creó la IA (`gh` no está instalado); solo redactó el título y la descripción. |

## 2026-11-06: Correcciones pedidas por el usuario

| # | Tipo | Qué ocurrió |
|---|---|---|
| 10 | Verificación previa | Se inspeccionó la imagen `apache/kafka:3.8.1`. Por defecto usa `log.dirs=/tmp/kraft-combined-logs`. `/var/lib/kafka/data` existe con dueño `appuser` (uid 1000, el usuario del contenedor). Las variables `KAFKA_*` se convierten en propiedades. Solo formatea el almacenamiento si no está formateado. `CLUSTER_ID` tiene un valor por defecto fijo. |
| 11 | Decisión de la IA | `KAFKA_LOG_DIRS=/var/lib/kafka/data` con el volumen `kafka-data`. Los metadatos KRaft quedan en el mismo directorio, porque `metadata.log.dir` toma el primer `log.dirs`. `CLUSTER_ID` quedó fijado con un valor generado por `kafka-storage.sh random-uuid`. |
| 12 | Decisión de la IA | Las migraciones dejaron de depender de `docker-entrypoint-initdb.d`, que solo corre con el volumen vacío. Ahora las aplica el servicio `migrate` (`db/migrate.sh`), que registra cada versión en `schema_migrations` y ejecuta cada archivo en una transacción. |
| 13 | Decisión de la IA | `event_id_conflicts` usa como clave primaria `(kafka_topic, kafka_partition, kafka_offset)`: el mismo mensaje reprocesado no se duplica, y otro mensaje conflictivo distinto sí queda registrado. Tiene FK a `ingested_events`. |
| 14 | Decisión de la IA | Refactor para pruebas controladas: `process_batch()` en el consumidor y la dependencia `get_producer` en la API. Si falla la DLQ se mantiene el comportamiento existente: excepción, ningún offset confirmado y reinicio del contenedor. No se agregaron reintentos. |
| 15 | Decisión de la IA | Se agregó `.gitattributes` con `*.sh eol=lf`, porque `core.autocrlf` en este equipo convertiría el script de migración a CRLF y no correría en Linux. |
| 16 | Verificación | Se aplicó la migración sobre el volumen existente `fleet_tsdb-data` sin borrarlo. La 001 se reaplicó sin cambios, la 002 creó la tabla y una segunda ejecución reportó "ya aplicada". |
| 17 | Error de la IA, corregido | El ejemplo del README y el script de verificación usaban `captured_at` `2026-11-06T10:00:00Z`, que era futuro en ese momento. La API lo rechazó con `422` y la primera verificación de persistencia no dejó mensaje pendiente. Se cambió a `2026-11-05T10:00:00Z` y se repitió. |
| 18 | Verificación | Con el consumidor detenido y lag 1, el contenedor de Kafka se eliminó y recreó (nuevo ID de contenedor, mismos volúmenes). Offsets y lag quedaron idénticos, y al arrancar el consumidor se procesó el pendiente. |
| 19 | Verificación | Suite en contenedor: 27 pruebas pasaron y AT-4 se omitió. AT-4 a mano: pasó. API con Kafka detenido: `503` en 5,2 s. |

## 2026-11-06: Etapa 2, portal en tiempo real (rama `feat/portal-tiempo-real`)

| # | Tipo | Qué ocurrió |
|---|---|---|
| 20 | Intervención | Se leyó el repositorio y el README. No hay archivos de instrucciones (`CLAUDE.md`, `AGENTS.md`). La rama se creó desde `706ae14`, que ya estaba en `origin/feat/ingesta-posiciones`. |
| 21 | Decisión de la IA | La ingesta se movió de `main.py` a `app/ingest.py`. La consulta quedó en `app/vehicles/` (`repository`, `snapshots`, `sse`, `routes`), con `db.py` y `settings.py` aparte. La prueba unitaria existente se ajustó para parchear `ingest.SEND_TIMEOUT_S`. |
| 22 | Decisión de la IA | Pool `psycopg_pool.AsyncConnectionPool` con `autocommit`, `check_connection` y `open(wait=False)`: la API arranca aunque la BD no responda y el hub informa `degraded`. |
| 23 | Decisión de la IA | Para detectar cambios se compara el contenido completo de la tabla, no `max(updated_at)`. `updated_at` toma la hora de inicio de la transacción, así que un `COMMIT` más tardío con un `now()` anterior podría pasar desapercibido. |
| 24 | Decisión de la IA | Buzón por cliente con como máximo un evento por tipo (`status`, `snapshot`), para que un cliente lento no acumule memoria. Límite de 500 clientes SSE por instancia. |
| 25 | Decisión de la IA | Umbral de antigüedad configurable en tiempo de ejecución: nginx genera `/config.json` con `envsubst` a partir de `PORTAL_STALE_AFTER_SECONDS`. |
| 26 | Intervención | Node 16 en el host no sirve para Vite 5. El `package-lock.json` se generó y el portal se construyó y probó en contenedores `node:20.18-alpine`. |
| 27 | Intervención | El puerto 8080 estaba ocupado por `TNSLSNR` (listener de Oracle) y `docker compose up` falló. No se tocó ese proceso: el portal usa `PORTAL_PORT`, 8088 por defecto. |
| 28 | Decisión de la IA | La coordenada por defecto de `make_event` en las pruebas pasó de Santiago de Chile a Bogotá. Los vehículos de pruebas anteriores siguen en la BD con coordenadas de Santiago y no se borraron. |
| 29 | Verificación | Pruebas en contenedor: 49 pasaron y AT-4 se omitió. Portal: tipos, build y 10 pruebas de Vitest pasaron. AT-4 a mano: pasó. |
| 30 | Verificación | Con el stack real y un cliente SSE abierto por el proxy se detuvo TimescaleDB. Llegaron `status: degraded` y heartbeats; al arrancarla, `status: ok` y un snapshot nuevo. El log registró el fallo una sola vez (`PoolTimeout`). |
| 31 | Verificación | Simulador: 240 s con 8 vehículos, 952 posiciones enviadas y 0 fallidas. En el servicio `simulator`, `docker compose stop` (SIGINT) terminó con código 0 y resumen. El reintento con el mismo `event_id` se probó con un servidor local controlado (`503`, `503`, `202`); no se provocó contra el stack real. |
| 32 | Verificación en navegador | En el navegador integrado de la app, con el simulador activo, se comprobó: actualización del detalle y del marcador sin recargar la página, selección desde la lista y desde el mapa, buscador, pérdida de conexión (proxy detenido) con datos conservados, reconexión con snapshot y selección conservada, y diseño a 375 px sin scroll horizontal. Una primera captura del mapa salió gris; se confirmó por JavaScript que las teselas habían terminado de cargar. |
| 33 | Corrección tras la verificación | Durante la desconexión el indicador seguía mostrando "Datos: al día". Se cambió a "Datos: último snapshot recibido" y se volvió a verificar en el navegador. También se ajustaron el formato del heartbeat (sin espacios, como los demás eventos) y el zoom al seleccionar un vehículo. |
| 34 | Verificación previa | Antes de implementar el timeout de lectura se inspeccionó `psycopg_pool` 3.2.4 dentro de la imagen de la API. Una conexión que vuelve cerrada (`UNKNOWN`) o en medio de una operación (`ACTIVE`) se descarta y se repone. Se confirmó también que `AsyncConnection.cancel_safe(timeout=)` existe en psycopg 3.2.3. |
| 35 | Decisión de la IA | `DB_READ_TIMEOUT_S` (3 s) en `VehicleRepository` con `asyncio.wait_for` sobre la adquisición más la consulta. Al cancelarse: `cancel_safe` (máximo 1 s), `close()` y devolución al pool, que la descarta. `statement_timeout` del servidor = timeout + 2 s como red de seguridad. `application_name = fleet-api` para identificar las conexiones. |
| 36 | Verificación | La primera ejecución de la suite dio 52 pasadas y 3 fallidas: dos timeouts de Kafka en pruebas de DLQ y conflictos, y una lectura de 5 s en el SSE del portal. La prueba nueva del bloqueo pasó. El fallo de la DLQ ocurrió antes de esa prueba. La suite tardó 188 s, frente a unos 50 habituales, y el stack llevaba unas 8 horas inactivo. Las tres pasaron al repetirlas y la suite completa siguiente dio 55 pasadas y 1 omitida (AT-4). No se identificó una causa más precisa. |
| 37 | Verificación | En los logs de la API durante la prueba con `LOCK TABLE`: tres lecturas vencidas, cada una con "Conexión descartada" y `discarding closed connection` del pool, y después "Lectura de posiciones recuperada". |
