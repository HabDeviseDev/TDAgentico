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
