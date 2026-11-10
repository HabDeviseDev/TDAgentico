# Entrega: matriz de cumplimiento

Estado del repositorio en la rama `feat/cierre-entrega`. Cada fila relaciona un requisito con su implementación, cómo demostrarlo, la evidencia disponible y el nivel de verificación alcanzado. Las referencias `#N` son entradas de [auditoria-ia.md](auditoria-ia.md).

- **Validación vigente:** [Validación del 2026-11-09](#validación-del-2026-11-09), sobre el commit `6235fd9`; salidas en [verificacion/2026-11-09/](verificacion/2026-11-09/).
- **Validación anterior:** [Verificación final (2026-11-08)](#verificación-final-2026-11-08), sobre el commit `1a2b3ae`; se conserva como histórico.
- **[Pruebas manuales del usuario](#pruebas-manuales-del-usuario-2026-11-09):** las hizo el usuario, no la IA.

**Niveles de verificación:**

- **Implementado:** el código existe y está documentado.
- **Dobles:** pruebas automáticas con dependencias simuladas (productor, BD, modelo, GPS, AWS CLI o Compose falsos), o con lógica pura.
- **Real:** ejecutado contra los servicios reales del stack local (Kafka, TimescaleDB, API, portal, agente) o en el navegador. **Real** no significa AWS, ni el proveedor del modelo, ni un dispositivo Android, salvo que se diga.

## Matriz

| Requisito | Implementación y archivos | Comando para demostrarlo | Evidencia | Verificación | Limitaciones y pendientes |
|---|---|---|---|---|---|
| **Ingesta asíncrona y persistencia especializada** | `POST /v1/positions` responde `202 Accepted` solo tras el ACK de Kafka (`acks=all`, productor idempotente); si Kafka no confirma a tiempo, `503`, y si el evento es inválido, `422`. `POST /v1/positions/batch` responde `200` con un resultado por evento (`accepted` tras su ACK, `rejected` o `retryable`), con `index` y `event_id`; `413`, `400` y `422` aplican al lote completo. El consumidor escribe en TimescaleDB en una transacción por lote y confirma el offset después; los inválidos van a la DLQ. Archivos: `services/api/app/ingest.py`, `services/consumer/consumer/{main,persistence}.py`, `contracts/fleet_contracts/`, `db/migrations/` | `docker compose up -d --build --wait` y el `curl` de [demo.md](demo.md#2-vehículos-en-el-mapa) | `tests/acceptance/test_ingestion.py` (AT-1 a AT-3), `test_dlq.py`, `test_event_id_conflicts.py`, `test_batch_ingestion.py`; AT-4, la caída entre COMMIT y offset, pasó a mano (#6, #19, #84). Verificación final: paso 03 | Implementado · Dobles (`test_consumer_dlq.py`, `test_api_kafka_errors.py`, `test_batch_ingest.py`) · **Real** | AT-4 se omite dentro del contenedor porque necesita la CLI de Docker, y en el cierre no se ejecutó a mano. Un solo broker sin replicación (ver AWS) |
| **Circuit breaker entre servicios** | Agente → API de flota: un circuito por dependencia (cerrado, abierto y semiabierto), con generaciones y reintentos acotados. Archivos: `services/agent/fleet_agent/{breaker,fleet_client}.py` | `docker compose stop api`, luego `curl -s localhost:8088/v1/chat/fleet-check` dos veces y `docker compose start api` ([demo.md](demo.md#4-circuit-breaker-sin-credencial)) | `test_agent_breaker.py`, `test_agent_contracts.py`. Con el stack real (#65): se abrió tras 3 timeouts en 6,9 s, rechazó en 0 ms y pasó por `half_open` → `closed` | Implementado · Dobles · **Real** (sin modelo, vía `fleet-check`) | El circuito es por instancia; no se comparte entre réplicas |
| **Agente y consultas en lenguaje natural** | LangChain con `langchain-anthropic` y un bucle propio con herramientas tipadas de solo lectura (vehículos, zonas, alertas). Contratos estrictos de respuesta y límites de pasos, llamadas, tiempo y concurrencia. Archivos: `services/agent/fleet_agent/` | Con credencial: `cp .env.example .env`, completar `ANTHROPIC_API_KEY`, `docker compose up -d agent` y preguntar en el portal | `test_agent_chat.py` (modelo guionado), `test_agent_contracts.py`. `test_agent_service.py` usa las herramientas reales contra la API real (#76) y comprueba `503 not_configured` sin credencial | Implementado · Dobles · Real con las herramientas y la API · **Real con el proveedor:** prueba manual del usuario ([pruebas manuales](#pruebas-manuales-del-usuario-2026-11-09)) y `test_proveedor_real` aprobada ([paso 12](#validación-del-2026-11-09)) | **Pendiente:** verificación manual, con el modelo real, de la hora America/Bogota y el Markdown en el texto (#152–#158; probados con dobles y una respuesta simulada). `test_selecciona_y_ejecuta_la_herramienta_real` falló de forma intermitente (#126) y en la pasada saturada del 2026-11-09 |
| **Mapa, alertas y chat** | Portal React + Leaflet servido por nginx (`services/portal/`). Alertas de detención mayor a 20 min dentro de una zona, con evaluación consistente y eventos atrasados (`services/api/app/alerts/`). Panel de chat | `docker compose --profile demo up -d simulator` y abrir http://localhost:8088; `docker compose --profile demo run --rm demo-alerts --once` | Vitest del portal; `test_stop_rule.py`, `test_alert_hub.py`, `test_alerts.py`. Navegador (#32, #48, #66): mapa, alertas, evidencia insuficiente, chat sin credencial y respuesta **simulada** con `fetch` interceptado | Implementado · Dobles · **Real** (navegador y stack) · **Manual del usuario:** actualización automática sin recargar y asistente respondiendo desde el portal | Pendiente: revisión manual, con el modelo real, de la hora local y el Markdown en el chat (hasta ahora solo con respuesta simulada, #156). No se midió el render en el navegador (#101) |
| **Actualizaciones SSE** | `GET /v1/vehicles/stream`: un hub de snapshots y otro de alertas por instancia, buzón acotado por cliente, heartbeat y estado `degraded`. nginx sin buffering. Archivos: `services/api/app/vehicles/{snapshots,sse}.py` | `curl -N localhost:8088/v1/vehicles/stream` | `test_snapshot_hub.py`, `test_read_timeout.py`, `test_vehicles_realtime.py`, `test_read_timeout_lock.py`. Sonda del portal: del 202 al snapshot p50 1,04 s y p95 1,56 s (#101) | Implementado · Dobles · **Real** | Lectura periódica de 1 s; para escalar, ver README "Costo de la lectura periódica" |
| **App móvil, SQLite y sincronización por lotes** | Expo 58 / React Native 0.88. Cola SQLite que sobrevive a reinicios, payload fijado al guardar, sincronización un envío a la vez con backoff y jitter, y controlador de captura con generaciones. Archivos: `services/mobile/src/core/`, `App.tsx` | `npm run e2e` contra la API real (comando en [demo.md](demo.md#6-persistencia-y-sincronización-móvil)) | 55 pruebas (#93) con SQLite real y dobles de GPS y endpoint. e2e real (#85): 3 aceptadas, 5 pendientes que sobreviven a cerrar la base, reenvío con 8 aceptadas y 0 duplicados en TimescaleDB. Verificación final: pasos 05 y 08 | Implementado · Dobles · **Real** (núcleo de la app con SQLite y API reales; red simulada) · **Manual del usuario en un Android con Expo Go:** posiciones simuladas, pendientes acumulados al detener la API y sincronizados al recuperarla, `MOVIL-WILL-001` visible en el portal ([pruebas manuales](#pruebas-manuales-del-usuario-2026-11-09)) | **Pendientes:** captura con GPS real; cierre y reapertura completamente sin red con la APK instalada. La captura es solo en primer plano |
| **Automatización de compilación Android** | `services/mobile/eas.json` (perfiles `development`, `preview` y `production`) y `.github/workflows/mobile.yml` (job `checks` en cada push o PR; `android-build` manual con `EXPO_TOKEN`). `scripts/check_workflow_order.py` | `eas build --platform android --profile preview`, o *Actions → mobile → Run workflow* | `expo export --platform android` genera el bundle (#84, #93; verificación final, paso 08). El orden de pasos del workflow está comprobado (#89; paso 09) | Implementado · Dobles (validación estática del workflow) | **Pendiente: ejecución de GitHub Actions y EAS.** Requiere una cuenta de Expo (`eas init`, `EXPO_TOKEN`); la IA no creó cuentas |
| **Carga con duplicados y errores** | Conjunto preparado con cuotas exactas 85/10/5 (nuevos, reenvíos e inválidos), k6 2.3.0 con tasa constante, clasificación por evento y reconciliación de solo lectura. Archivos: `loadtest/` | `API_URL=http://api:8000 loadtest/lt.sh load load-300 300 50 120 11` y `loadtest/lt.sh reconcile load-300 180` | `loadtest/reports/2026-11-07/` y `test_loadtest_prepare.py`, `test_reconcile.py`. Reconciliación OK en todas las ejecuciones: sin pérdidas ni duplicados; los ambiguos terminaron persistidos | Implementado · Dobles · **Real** (stack local) | **No aprobada: incumplimiento de rendimiento.** k6 terminó con código 99 (umbrales incumplidos) en `load-300` (50/s: p95 1,76 s frente a 500 ms; 75 ambiguos; 109 descartadas), `short-02` (en frío: 54 ambiguos, 32 descartadas) y `short-03` (2 descartadas frente a 0). Solo `batch-100` cumplió (código 0). Medido solo en local (4 CPU, 3,78 GiB), el 2026-11-07; no se repitió. Sin medición en AWS. Detalle en [Resultados de carga](#resultados-de-carga-existentes) |
| **Escenarios de caos y reconciliación** | `loadtest/chaos.sh` (consumidor, TimescaleDB, Kafka) sobre el proyecto aislado `fleet-chaos`, y reconciliación final. `ResilientProducer` reemplaza el productor tras un error fatal | `loadtest/chaos.sh kafka` (parámetros en el README) | Reportes `ch-*` en `loadtest/reports/2026-11-07/`. A y B OK (#104). C encontró el defecto del productor (#105) y se corrigió: 905/905 reenvíos confirmados sin reiniciar la API (#114). `test_producer_replacement.py` | Implementado · Dobles · **Real** | Queda la tarea auxiliar de aiokafka, cosmética (`Task was destroyed but it is pending!`, #114). En el cierre la API del stack principal se reconstruyó con la corrección (paso 01), pero el caos no se repitió |
| **Docker Compose y Terraform** | `docker-compose.yml` (perfiles `demo` y `test`). `infra/terraform/`: VPC, EC2 AL2023, IAM con SSM, EBS cifrado con snapshots y alarmas. `infra/deploy/` (override sin puertos internos, Caddy, despliegue por SHA, respaldo y restauración con metadatos) | `docker compose up -d --build --wait`; `infra/scripts/validate.sh all` | Paso 01 de la verificación final. `fmt`, `init`, `validate`, `terraform test` (4/4), TFLint, Trivy, ShellCheck, Compose y puertos, Caddy, instalación en AL2023 y respaldo y restauración simulados (33/33) (#119–#135; paso 10) | Compose: Implementado · **Real**. Terraform: Implementado · Dobles y estática | **Pendiente: plan, despliegue y restauración real en AWS.** Sin cuenta ni credenciales. El workflow `infra` no ha corrido en GitHub. Una sola instancia en una AZ (ver [arquitectura-aws.md](arquitectura-aws.md)) |
| **Auditoría de IA** | [auditoria-ia.md](auditoria-ia.md): entradas numeradas por etapa (decisiones, intervenciones, errores de la IA, verificaciones y pendientes). Casos resumidos en el [README](../README.md#auditoría-de-ia-casos-para-evaluar) | — | El registro completo, con cada entrada ligada a su commit y evidencia | Documentado | Registra solo lo ocurrido. Algunos detalles no quedaron registrados en su momento: por ejemplo, cuál prueba falló en #107 |
| **Video y demostración del entorno agéntico** | [entorno-agentico.md](entorno-agentico.md) (recorrido de 2 min), [CLAUDE.md](../CLAUDE.md) (agregado en el cierre) y [demo.md](demo.md). El guion del video es material personal del usuario y no se versiona | Seguir `demo.md` | Este cierre | Preparado | **Pendiente: grabar y publicar el video.** Lo hace el usuario; el enlace irá en el README |

## Pendientes visibles

| Pendiente | Estado al cierre | Evidencia o alternativa |
|---|---|---|
| Chat con el proveedor real | **Resuelto el 2026-11-09:** prueba manual del usuario en el portal y prueba automática `test_proveedor_real` aprobada (paso 12) | **Pendiente:** revisar, con el modelo real, la hora America/Bogota y el Markdown del texto (ver [pruebas manuales pendientes](#pruebas-manuales-pendientes)) |
| Captura y cierre real en Android | **Parcial.** El usuario probó la app en un Android con Expo Go, con posiciones simuladas, y la sincronización tras detener y recuperar la API | **Pendientes:** captura con GPS real; cierre y reapertura completamente sin red con la APK instalada (requiere EAS) |
| Ejecución de GitHub Actions y EAS | **Pendiente.** Sin cuenta de Expo y sin push de esta rama | Validación local del orden de pasos (paso 09) y `expo export` (paso 08). Al hacer push se ejecutan `mobile.yml` (`checks`) e `infra.yml` |
| Plan, despliegue y restauración real en AWS | **Pendiente.** Sin cuenta | Validación sin credenciales (paso 10) y respaldo y restauración con simuladores (33/33). Procedimiento en [infra/README.md](../infra/README.md) |
| Tarea auxiliar de aiokafka | **Pendiente** (cosmético) | #114; no afecta la ingesta |
| Capacidad medida y objetivos de rendimiento | **No aprobado.** `load-300`, `short-02` y `short-03` incumplieron los umbrales (k6 con código 99) | Ver [Resultados de carga](#resultados-de-carga-existentes). Solo la ingesta por lotes (`batch-100`) cumplió |
| Repetir la aceptación con la API reconstruida | **Resuelto en el cierre** | Pasos 01 a 03: API reconstruida con `ResilientProducer`; 236 pasadas y 0 fallidas. Estaba abierto desde #115 |
| `terraform validate` | **Resuelto** el 2026-11-08 (#131) | Repetido en el paso 10 |

## Validación del 2026-11-09

- **Código bajo prueba:** commit `6235fd9` (rama `feat/cierre-entrega`).
- **Cambios sin confirmar durante la validación:**
  - Las salidas de `docs/verificacion/2026-11-09/`.
  - `.dockerignore`, que excluye `**/node_modules` y `services/mobile/.expo` del contexto de Docker. Ninguna imagen usa esos archivos; se agregó para desbloquear la construcción de la imagen de pruebas (ver más abajo).
- **Entorno:** Windows con Docker Desktop. Docker 29.9.2, Compose v5.6.1, 4 CPU y 3,78 GiB para Docker.
- **Fechas y horas:** el intervalo cruza la medianoche UTC, así que hay logs con las dos fechas. La fuente son las marcas `# inicio:` y `# fin:` de cada log.

  | Tramo | UTC | Hora de Bogotá (UTC−5) |
  |---|---|---|
  | Toda la validación, incluidos los intentos fallidos | 2026-11-09 21:04:58 → 2026-11-10 01:04:02 | 2026-11-09 16:04:58 → 20:04:02 |
  | Pasada definitiva (pasos 01 a 11) | 2026-11-09 23:17:59 → 2026-11-10 00:53:29 | 2026-11-09 18:17:59 → 19:53:29 |
  | Paso 12 (proveedor real) | 2026-11-10 00:53:58 → 01:04:02 | 2026-11-09 19:53:58 → 20:04:02 |

  En hora de Bogotá todo ocurrió el 2026-11-09; por eso la carpeta y la validación llevan esa fecha. Los pasos 08 a 12 tienen fecha UTC del 2026-11-10.
- **Comando:**

  ```bash
  scripts/verificacion_final.sh 2026-11-09 01 02 03 04 05 06 07 08 09 10 11
  ```

  El paso 00 (entorno) es de la primera pasada (`scripts/verificacion_final.sh 2026-11-09`).
- **Salidas:** [verificacion/2026-11-09/](verificacion/2026-11-09/), con un `.log` por paso y [resumen.tsv](verificacion/2026-11-09/resumen.tsv). Los intentos fallidos se conservan con los sufijos `-interrumpido`, `-saturado` y `-kafka-no-sano`.

### Resultados

| Paso | Qué | Resultado | Duración |
|---|---|---|---|
| 01 | `docker compose up -d --build --wait` | OK, todo sano | 341 s |
| 02 | La API contiene `ResilientProducer` | OK | 24 s |
| 03 | Unitarias y aceptación contra el stack real | **249 pasadas, 0 fallidas, 36 omitidas** (216 unitarias y 33 de aceptación pasadas); 1 aviso de deprecación (`starlette`/`anyio`) | 529 s |
| 04 | `demo-alerts --once` | Los 6 escenarios dieron el resultado esperado (ver abajo) | 58 s |
| 05 | e2e móvil (núcleo de la app con SQLite real contra la API real; red simulada) | 1 de 1: 3 aceptadas, 5 pendientes que sobreviven al cierre de la base, respuesta perdida y 8 aceptadas al reenviar (`MOVIL-DEMO-9f382a`) | 880 s |
| 05b | Duplicados en la base | `MOVIL-DEMO-9f382a`: 8 filas de historial, 8 `event_id` distintos, 8 en `ingested_events` | 14 s |
| 06 | `docker compose stop`, sin borrar volúmenes | OK | 31 s |
| 07 | Portal: `tsc`, `vite build` y Vitest, sin caché | OK: 39 pruebas | 366 s |
| 08 | App: `npm ci`, `tsc`, Vitest y `expo export --platform android` | OK: 55 pruebas; bundle Android de 683 módulos | 312 s |
| 09 | Orden de pasos de los workflows | OK | 28 s |
| 10 | `infra/scripts/validate.sh all` (sin credenciales ni recursos) | **OK (all):** `fmt`; `init`; `validate`; `terraform test` 4/4; `user_data` en 4 casos; TFLint; Trivy (dos modos); ShellCheck; Compose y puertos; respaldo y restauración simulados 33/33; `verificacion_final.sh` simulado 4/4; Caddy; instalación en AL2023 (Docker 25.0.14, Compose v5.6.1, Buildx v0.38.1) | 3073 s |
| 11 | `test_check_compose_ports.py` | 7 de 7 | 71 s |
| 12 | **Proveedor real.** Se ejecutó aparte, con autorización del usuario, porque consume créditos de su cuenta de Anthropic. Comando: `docker compose up -d --wait` y luego `AGENT_REAL_PROVIDER_TEST=1 docker compose --profile test run --rm -T tests tests/acceptance/test_agent_service.py -v -rfEs` | **3 de 3.** `test_proveedor_real` hace una consulta real ("¿Qué zonas de demostración están activas?") y comprueba `answered`, el uso de `consultar_zonas`, `DEMO-Z01` y la fuente con hora. **No comprueba la hora local ni el Markdown del texto** | 605 s (incluye levantar el stack) |

**Lo que pediste validar y dónde quedó cubierto (paso 03, salvo que se indique):**

| Área | Pruebas | Tipo |
|---|---|---|
| Ingesta, duplicados y validaciones | `test_ingestion.py` (AT-1 a AT-3: ingesta, reenvío, posición antigua y empate), `test_batch_ingestion.py`, `test_event_id_conflicts.py`, `test_dlq.py`; `test_batch_ingest.py`, `test_api_kafka_errors.py` | Real y dobles |
| Recuperación | `test_producer_replacement.py` (productor Kafka), `test_consumer_dlq.py` (orden DLQ → COMMIT → offset), `test_read_timeout*.py` (BD bloqueada: `503`, degradado y recuperación), `test_snapshot_hub.py` | Dobles y real |
| Agente: herramientas y fuentes | `test_agent_chat.py::test_selecciona_y_ejecuta_la_herramienta_real`, `test_agent_service.py::test_herramientas_reales_contra_la_api` (API real) | Dobles y real |
| Agente: vehículo inexistente | `test_agent_chat.py::test_consulta_sin_coincidencias` (`no_matches`) y la regresión `BOG-001`/`bog-001` en `test_agent_contracts.py` | Dobles |
| Agente: datos desactualizados | `test_agent_timezone.py` (`age_s` 246 y `stale` con advertencia `stale_data`), `test_agent_chat.py` | Dobles |
| Agente: America/Bogota | `test_agent_timezone.py` (9 pruebas: 15:26:28Z → `09/11/2026 10:26:28 America/Bogota`, cambio de día, UTC conservado) | Dobles |
| Markdown | `ChatMarkdown.test.tsx` y `markdown.test.ts` (paso 07): negritas, listas, saltos de línea, HTML y scripts escapados | Render con React |
| Alertas: detención válida | `test_stop_rule.py` (más de 20 min alerta; exactamente 20 no), `test_alerts.py` (25 min detenido en zona), paso 04 `ZONA25` | Dominio y real |
| Alertas: exclusión por zona | `test_stop_rule.py::test_detenido_fuera_de_toda_zona_no_alerta`, `::test_salir_de_la_zona_rompe_el_tramo`, paso 04 `FUERA` | Dominio y real |
| Alertas: evidencia insuficiente | `test_stop_rule.py::test_ultima_muestra_desactualizada_da_evidencia_insuficiente`, paso 04 `SINDATOS` (`stale_last_sample`) | Dominio y real |
| Mobile | Pasos 05, 05b y 08 | Real (núcleo) y dobles |
| Infraestructura sin desplegar | Paso 10 | Estática y simuladores |

**Paso 04, salida real** (`04-demo-alertas.log`):

- `ZONA25`: alerta en `DEMO-Z01`.
- `FUERA`, `MOVIL` y `HUECO`: sin alerta.
- `SINDATOS`: evidencia insuficiente (`stale_last_sample`).
- `ATRASADO`: alerta y luego retirada por el evento atrasado.

**Omitidas en el paso 03:**

| Pruebas | Motivo | Cubiertas en |
|---|---|---|
| 33 de `test_fleet_backup_restore.py` | La imagen de pruebas no tiene `jq` | Paso 10 (`validate.sh backup`) |
| `test_check_compose_ports.py` (7, contadas como 1 omisión de módulo) | La imagen de pruebas no copia `infra/` | Paso 11 |
| AT-4 (`test_crash_recovery.py`) | Necesita la CLI de Docker dentro del contenedor | **No ejecutada.** Pasó a mano en etapas anteriores (#6, #19, #29, #49, #84) |
| `test_agent_service.py::test_proveedor_real` | Opcional; requiere `AGENT_REAL_PROVIDER_TEST=1` | Paso 12, ejecutado aparte con autorización del usuario: **pasó** |

### Intentos fallidos de esta validación (se conservan)

1. **Primera pasada, paso 03 interrumpido** (`03-…-interrumpido.log`): tras unos 40 min seguía construyendo la imagen de pruebas, porque copiaba `services/mobile/node_modules` (420 MB, instalados en el host durante la prueba con Expo Go). La IA la detuvo y agregó esas rutas a `.dockerignore`.
2. **Pasada saturada** (`*-saturado.log`):
   - **Resultado:** 14 fallidas y 235 pasadas en el paso 03; el paso 04 también falló.
   - **Fallos:** casi todos `503` de la API por lecturas que superaron 3 s o porque Kafka no confirmó en 5 s, más esperas agotadas en el SSE.
   - **Causa:** la ejecución anterior seguía viva en segundo plano y su construcción corría en paralelo. La carga de la VM de Docker llegó a 46 con 4 CPU y Kafka quedó `unhealthy`. La IA no la había detenido del todo: `TaskStop` no terminó los procesos hijos de Git Bash.
   - **No indica un defecto del código:** al repetir con el entorno limpio, las mismas pruebas pasaron.
3. **Arranque con Kafka no sano** (`*-kafka-no-sano.log`): `docker compose up --wait` falló porque la comprobación de salud de Kafka venció mientras arrancaba (síntoma ya registrado en #111). Kafka quedó sano minutos después y la pasada siguiente arrancó bien.

### Resultados de carga existentes

No se repitieron en esta validación: son del 2026-11-07 (`loadtest/reports/2026-11-07/`). El código de salida 99 de k6 significa que se incumplió al menos un umbral (p95 500 ms, p99 1,5 s, 0 ambiguos, 0 descartadas, 0 inesperados).

| Ejecución | k6 | Umbrales | Reconciliación |
|---|---|---|---|
| `short-02` (20 veh, 10/s, en frío) | 99 | **Incumplidos:** 54 ambiguos (503), 32 descartadas, p95 5,10 s | OK: las 44 ambiguas estaban persistidas |
| `short-03` (20 veh, 10/s, en caliente) | 99 | **Incumplidos:** 2 descartadas (p95 209 ms y 0 ambiguos, dentro del objetivo) | OK |
| `load-300` (300 veh, 50/s, 120 s) | 99 | **Incumplidos:** p95 1,76 s, p99 5,01 s, 75 ambiguos, 109 descartadas | OK: sin pérdidas ni duplicados |
| `batch-100` (100 veh, lotes de 25) | 0 | **Cumplidos** | OK |
| Caos A (consumidor) y B (TimescaleDB) | 0 | Cumplidos | OK |
| Caos C (Kafka detenido), las tres ejecuciones | 99 en la carga (esperado: Kafka detenido); 0 en los reenvíos | No aplica durante la falla | OK; con la corrección, 905/905 reenvíos confirmados sin reiniciar la API |

**Conclusión:** el objetivo de rendimiento con el endpoint individual **no está cumplido** en este equipo. La garantía de datos sí se cumplió en todas las ejecuciones: sin pérdidas ni duplicados, y los ambiguos terminaron persistidos.

## Pruebas manuales del usuario (2026-11-09)

Las hizo el usuario y las informa él; la IA no las ejecutó ni tiene capturas.

| Prueba | Resultado informado |
|---|---|
| App en un celular Android con Expo Go | Funcionó |
| Envío de posiciones simuladas | Funcionó |
| Acumulación de pendientes al detener la API | Funcionó |
| Sincronización de pendientes al recuperar la API | Funcionó |
| Vehículo `MOVIL-WILL-001` visible en el portal | Funcionó |
| Actualización automática del portal sin recargar | Funcionó |
| Asistente operativo respondiendo desde el portal (proveedor real) | Funcionó |

**No realizadas, o sin evidencia:**

- Captura con GPS real.
- Cierre y reapertura completamente sin red con la APK instalada.
- Despliegue y restauración reales en AWS.
- Verificación manual de los últimos ajustes de hora y Markdown con el modelo real.

## Pruebas manuales pendientes

Con el stack levantado (`docker compose up -d --build --wait`):

1. **Hora America/Bogota y Markdown con el modelo real:**
   1. En http://localhost:8088, abre **Asistente** y pregunta: "¿Cuál es la última posición del vehículo MOVIL-WILL-001?".
   2. **Esperado:** las horas aparecen como `dd/mm/aaaa hh:mm:ss America/Bogota`, iguales a las del panel del vehículo; no hay horas UTC en el texto; las negritas y listas se ven formateadas, sin `**`. Al pasar el cursor por la hora de una fuente se ve su UTC original.
   3. Si el vehículo está desactualizado, aparece la advertencia "Posición desactualizada".
2. **GPS real:**
   1. En la app, desactiva el modo simulado, concede el permiso de ubicación e inicia la captura al aire libre.
   2. En el portal, el vehículo debe moverse con coordenadas reales.
3. **Cierre y reapertura sin red con APK:**
   1. Compila con `cd services/mobile` y `eas build --platform android --profile preview` (requiere una cuenta de Expo).
   2. Instala la APK y captura con conexión.
   3. Activa el modo avión, captura y cierra la app por completo.
   4. Reábrela sin red: los pendientes siguen ahí.
   5. Desactiva el modo avión: se sincronizan.
   6. Comprueba que no haya duplicados (ambos números deben coincidir):

      ```bash
      docker compose exec timescaledb psql -U fleet -d fleet -c "SELECT count(*), count(DISTINCT event_id) FROM position_history WHERE vehicle_id = 'MOVIL-WILL-001'"
      ```

4. **AWS:** sigue [infra/README.md](../infra/README.md), secciones "Comandos" y "Respaldo y restauración". Requiere una cuenta y crea recursos con costo.

### Cómo reproducir esta validación

```bash
docker compose stop
```

```bash
scripts/verificacion_final.sh 2026-11-09
```

Ejecuta la verificación sin nada más corriendo en Docker, sin el simulador ni otra verificación en paralelo. Si `docker compose up --wait` falla porque Kafka está `unhealthy`, espera a que quede sano (`docker compose ps`) y repite los pasos con `scripts/verificacion_final.sh <fecha> 01 02 03 04 05 06 07 08 09 10 11`.

**Paso 12, proveedor real** (consume créditos de la cuenta de Anthropic configurada en `.env`):

```bash
docker compose up -d --wait
```

```bash
AGENT_REAL_PROVIDER_TEST=1 docker compose --profile test run --rm -T tests tests/acceptance/test_agent_service.py -v -rfEs
```

**En PowerShell**, el script de verificación es de bash y se ejecuta con Git Bash. La variable se fija con `$env:` y se quita después:

```powershell
& "C:\Program Files\Git\bin\bash.exe" scripts/verificacion_final.sh 2026-11-09
```

```powershell
$env:AGENT_REAL_PROVIDER_TEST = "1"; docker compose --profile test run --rm -T tests tests/acceptance/test_agent_service.py -v -rfEs; Remove-Item Env:AGENT_REAL_PROVIDER_TEST
```

En el cierre se comprobó que `$env:AGENT_REAL_PROVIDER_TEST` llega a la configuración de Compose (`docker compose --profile test config`) y que Git Bash responde desde PowerShell con `bash.exe -c`. La llamada con la ruta del script no se ejecutó desde PowerShell, y la validación del 2026-11-09 se hizo desde Git Bash.

### Secretos

- **Archivos versionados:** sin claves. Se buscaron patrones de claves de Anthropic, AWS, GitHub, Slack y Expo, y claves privadas. Solo aparecen nombres de variables, valores ficticios y la contraseña de desarrollo local `fleet` del Compose.
- **Historial:** ningún `.env`, credencial ni estado de Terraform en todo el historial de git.
- **`.env` local:** existe, contiene la credencial del usuario y está ignorado por `.gitignore`. No se leyó su contenido.
- **Logs de verificación:** sin claves. El agente informa `configured: true` sin mostrar el valor.

## Verificación final (2026-11-08)

> **Histórico.** Esta sección conserva sin cambios los resultados del 2026-11-08 sobre el commit `1a2b3ae`. Lo que aquí figura como pendiente del proveedor real y de Android se actualizó en la [validación del 2026-11-09](#validación-del-2026-11-09): el proveedor real quedó comprobado y Android, parcialmente, con Expo Go. Siguen pendientes la APK sin red, el GPS real, Actions y EAS, y AWS.

- **Código bajo prueba:** commit `1a2b3ae` (la punta de `feat/infraestructura-aws`). Los cambios del cierre que no estaban confirmados eran documentación y el propio script de verificación, sin código de servicios.
- **Entorno:** Windows con Docker Desktop. Docker 29.9.2, Compose v5.6.1, 4 CPU y 3,78 GiB para Docker.
- **Comando:**

  ```bash
  scripts/verificacion_final.sh 2026-11-08
  ```

- **Salidas completas:** [verificacion/2026-11-08/](verificacion/2026-11-08/), un `.log` por paso y [resumen.tsv](verificacion/2026-11-08/resumen.tsv) con el código y la duración.
- **Control de errores** (corregido después de esta ejecución; auditoría #147):
  - Cada paso corre con `errexit` y `pipefail`, así que un comando intermedio que falla hace fallar el paso.
  - Los pasos siguientes se ejecutan igual.
  - El script termina con código 1 y lista los pasos fallidos.
  - La ejecución del 2026-11-08 fue con la versión anterior. En ella todos los pasos dieron código 0, pero esa versión no detectaba un comando intermedio fallido si el último comando del bloque terminaba bien. Los logs muestran la salida completa de cada comando, y ninguno muestra errores en comandos intermedios.
- **Repetición de pasos:** los pasos 05b, 07 (sin caché) y 11 se agregaron al script después de la primera pasada y se ejecutaron con `scripts/verificacion_final.sh 2026-11-08 05b 06 07 11`.

| Paso | Qué | Resultado | Duración |
|---|---|---|---|
| 01 | `docker compose up -d --build --wait`: reconstruye todas las imágenes, incluida la API con la corrección del productor | OK, todo sano | 323 s |
| 02 | La API reconstruida contiene `ResilientProducer` | `ResilientProducer presente: True` | 23 s |
| 03 | Unitarias y aceptación contra el stack real (`docker compose --profile test run --rm --build tests -v -rfEs`) | **236 pasadas, 0 fallidas, 36 omitidas** (203 unitarias y 33 de aceptación pasadas); 1 aviso de deprecación (`starlette`/`anyio`) | 628 s |
| 04 | `demo-alerts --once` | Los 6 escenarios dieron el resultado esperado; `ATRASADO` pasó de alerta a sin alerta tras el evento atrasado | 66 s |
| 05 | e2e móvil (núcleo de la app con SQLite real contra la API real) | 1 de 1: 3 aceptadas, 5 pendientes que sobrevivieron al cierre de la base, respuesta perdida, 8 aceptadas al reenviar | 210 s |
| 05b | Duplicados en la base para `MOVIL-DEMO-742a72` | 8 filas de historial, 8 `event_id` distintos y 8 en `ingested_events` | 33 s |
| 06 | `docker compose stop`, sin borrar volúmenes | OK | 25 s |
| 07 | Portal: `tsc`, `vite build` y Vitest (`docker build --no-cache --target test services/portal`) | OK, 23 pruebas. La primera pasada, con caché, no reejecutó `tsc` ni `vite build` (`07-…-con-cache.log`); por eso se repitió sin caché | 211 s |
| 08 | App: `npm ci`, `tsc`, Vitest y `expo export --platform android` | OK: 55 pruebas; bundle Android de 683 módulos | 238 s |
| 09 | Orden de pasos en `.github/workflows/*.yml` | OK | 21 s |
| 10 | `infra/scripts/validate.sh all` | **OK (all):** `fmt`; `init`; `validate` (`Success! The configuration is valid.`); `terraform test` 4/4; `user_data` en 4 casos; TFLint; Trivy (dos modos); ShellCheck; Compose y puertos; respaldo y restauración simulados 33/33; Caddy; instalación en AL2023 (Docker 25.0.14, Compose v5.6.1, Buildx v0.38.1) | 1002 s |
| 11 | `test_check_compose_ports.py` (omitido en el paso 03 porque la imagen de pruebas no copia `infra/`) | 7 de 7 | 52 s |

**Omitidas en el paso 03, con su motivo:**

| Pruebas | Motivo | Cubiertas en |
|---|---|---|
| 33 de `test_fleet_backup_restore.py` | La imagen de pruebas no tiene `jq` | Paso 10 (`validate.sh backup`): 33/33 |
| 7 de `test_check_compose_ports.py`, contadas como 1 omisión de módulo | La imagen de pruebas no copia `infra/` | Paso 11: 7/7 |
| AT-4 (`test_crash_recovery.py`) | Necesita la CLI de Docker dentro del contenedor | **No ejecutada en el cierre.** Pasó a mano en etapas anteriores (#6, #19, #29, #49, #84) |
| Proveedor real (`test_agent_service.py`) | Sin `AGENT_REAL_PROVIDER_TEST=1` ni credencial | **Pendiente** |

**No ejecutado en el cierre:**

- AT-4 a mano.
- Chat con el proveedor real.
- La app en Android.
- GitHub Actions y EAS.
- `terraform plan`, el despliegue y la restauración en AWS.
- Las pruebas de carga y de caos. Sus resultados son del 2026-11-07 (`loadtest/reports/2026-11-07/`); la carga no se repitió con la API reconstruida.
- La verificación en el navegador. Las verificaciones en el navegador son de las etapas 2 a 5 (#32, #48, #66, #81).
