# Entrega: matriz de cumplimiento

Estado del repositorio en la rama `feat/cierre-entrega`. Cada fila relaciona un requisito con su implementación, cómo demostrarlo, la evidencia disponible y el nivel de verificación alcanzado. Las referencias `#N` son entradas de [auditoria-ia.md](auditoria-ia.md); la verificación final del cierre está en [verificacion/2026-11-08/](verificacion/2026-11-08/) (sección [Verificación final](#verificación-final-2026-11-08)).

**Niveles de verificación:**

- **Implementado:** el código existe y está documentado.
- **Dobles:** pruebas automáticas con dependencias simuladas (productor, BD, modelo, GPS, AWS CLI o Compose falsos), o con lógica pura.
- **Real:** ejecutado contra los servicios reales del stack local (Kafka, TimescaleDB, API, portal, agente) o en el navegador. **Real** no significa AWS, ni el proveedor del modelo, ni un dispositivo Android, salvo que se diga.

## Matriz

| Requisito | Implementación y archivos | Comando para demostrarlo | Evidencia | Verificación | Limitaciones y pendientes |
|---|---|---|---|---|---|
| **Ingesta asíncrona y persistencia especializada** | `POST /v1/positions` y `/v1/positions/batch` responden `202` tras el ack de Kafka (`acks=all`, productor idempotente). El consumidor escribe en TimescaleDB en una transacción por lote y confirma el offset después; los inválidos van a la DLQ. Archivos: `services/api/app/ingest.py`, `services/consumer/consumer/{main,persistence}.py`, `contracts/fleet_contracts/`, `db/migrations/` | `docker compose up -d --build --wait` y el `curl` de [demo.md](demo.md#2-vehículos-en-el-mapa) | `tests/acceptance/test_ingestion.py` (AT-1 a AT-3), `test_dlq.py`, `test_event_id_conflicts.py`, `test_batch_ingestion.py`; AT-4, la caída entre COMMIT y offset, pasó a mano (#6, #19, #84). Verificación final: paso 03 | Implementado · Dobles (`test_consumer_dlq.py`, `test_api_kafka_errors.py`, `test_batch_ingest.py`) · **Real** | AT-4 se omite dentro del contenedor porque necesita la CLI de Docker, y en el cierre no se ejecutó a mano. Un solo broker sin replicación (ver AWS) |
| **Circuit breaker entre servicios** | Agente → API de flota: un circuito por dependencia (cerrado, abierto y semiabierto), con generaciones y reintentos acotados. Archivos: `services/agent/fleet_agent/{breaker,fleet_client}.py` | `docker compose stop api`, luego `curl -s localhost:8088/v1/chat/fleet-check` dos veces y `docker compose start api` ([demo.md](demo.md#4-circuit-breaker-sin-credencial)) | `test_agent_breaker.py`, `test_agent_contracts.py`. Con el stack real (#65): se abrió tras 3 timeouts en 6,9 s, rechazó en 0 ms y pasó por `half_open` → `closed` | Implementado · Dobles · **Real** (sin modelo, vía `fleet-check`) | El circuito es por instancia; no se comparte entre réplicas |
| **Agente y consultas en lenguaje natural** | LangChain con `langchain-anthropic` y un bucle propio con herramientas tipadas de solo lectura (vehículos, zonas, alertas). Contratos estrictos de respuesta y límites de pasos, llamadas, tiempo y concurrencia. Archivos: `services/agent/fleet_agent/` | Con credencial: `cp .env.example .env`, completar `ANTHROPIC_API_KEY`, `docker compose up -d agent` y preguntar en el portal | `test_agent_chat.py` (modelo guionado), `test_agent_contracts.py`. `test_agent_service.py` usa las herramientas reales contra la API real (#76) y comprueba `503 not_configured` sin credencial | Implementado · Dobles · Real **sin el modelo** | **Pendiente: chat con el proveedor real.** No hubo credencial en ninguna etapa (#68). Prueba opcional: `AGENT_REAL_PROVIDER_TEST=1`. `test_selecciona_y_ejecuta_la_herramienta_real` falló una vez de forma intermitente (#126) |
| **Mapa, alertas y chat** | Portal React + Leaflet servido por nginx (`services/portal/`). Alertas de detención mayor a 20 min dentro de una zona, con evaluación consistente y eventos atrasados (`services/api/app/alerts/`). Panel de chat | `docker compose --profile demo up -d simulator` y abrir http://localhost:8088; `docker compose --profile demo run --rm demo-alerts --once` | Vitest del portal; `test_stop_rule.py`, `test_alert_hub.py`, `test_alerts.py`. Navegador (#32, #48, #66): mapa, alertas, evidencia insuficiente, chat sin credencial y respuesta **simulada** con `fetch` interceptado | Implementado · Dobles · **Real** (navegador y stack) | El chat con respuesta real del modelo está pendiente. No se midió el render en el navegador (#101) |
| **Actualizaciones SSE** | `GET /v1/vehicles/stream`: un hub de snapshots y otro de alertas por instancia, buzón acotado por cliente, heartbeat y estado `degraded`. nginx sin buffering. Archivos: `services/api/app/vehicles/{snapshots,sse}.py` | `curl -N localhost:8088/v1/vehicles/stream` | `test_snapshot_hub.py`, `test_read_timeout.py`, `test_vehicles_realtime.py`, `test_read_timeout_lock.py`. Sonda del portal: del 202 al snapshot p50 1,04 s y p95 1,56 s (#101) | Implementado · Dobles · **Real** | Lectura periódica de 1 s; para escalar, ver README "Costo de la lectura periódica" |
| **App móvil, SQLite y sincronización por lotes** | Expo 58 / React Native 0.88. Cola SQLite que sobrevive a reinicios, payload fijado al guardar, sincronización un envío a la vez con backoff y jitter, y controlador de captura con generaciones. Archivos: `services/mobile/src/core/`, `App.tsx` | `npm run e2e` contra la API real (comando en [demo.md](demo.md#6-persistencia-y-sincronización-móvil)) | 55 pruebas (#93) con SQLite real y dobles de GPS y endpoint. e2e real (#85): 3 aceptadas, 5 pendientes que sobreviven a cerrar la base, reenvío con 8 aceptadas y 0 duplicados en TimescaleDB. Verificación final: pasos 05 y 08 | Implementado · Dobles · **Real** (núcleo de la app con SQLite y API reales; red simulada) | **Pendiente: captura y cierre real en Android** (teléfono o emulador). La captura es solo en primer plano |
| **Automatización de compilación Android** | `services/mobile/eas.json` (perfiles `development`, `preview` y `production`) y `.github/workflows/mobile.yml` (job `checks` en cada push o PR; `android-build` manual con `EXPO_TOKEN`). `scripts/check_workflow_order.py` | `eas build --platform android --profile preview`, o *Actions → mobile → Run workflow* | `expo export --platform android` genera el bundle (#84, #93; verificación final, paso 08). El orden de pasos del workflow está comprobado (#89; paso 09) | Implementado · Dobles (validación estática del workflow) | **Pendiente: ejecución de GitHub Actions y EAS.** Requiere una cuenta de Expo (`eas init`, `EXPO_TOKEN`); la IA no creó cuentas |
| **Carga con duplicados y errores** | Conjunto preparado con cuotas exactas 85/10/5 (nuevos, reenvíos e inválidos), k6 2.3.0 con tasa constante, clasificación por evento y reconciliación de solo lectura. Archivos: `loadtest/` | `API_URL=http://api:8000 loadtest/lt.sh load load-300 300 50 120 11` y `loadtest/lt.sh reconcile load-300 180` | `loadtest/reports/2026-11-07/` y `test_loadtest_prepare.py`, `test_reconcile.py`. Reconciliación OK en todas las ejecuciones: sin pérdidas ni duplicados; los ambiguos terminaron persistidos | Implementado · Dobles · **Real** (stack local) | **Incumplimiento de rendimiento:** `load-300` (50/s) **no cumplió** los umbrales (p95 1,76 s frente a 500 ms; 75 ambiguos; 109 descartadas), ni `short-02` en frío. Sí cumplió `batch-100`. Medido solo en local (4 CPU, 3,78 GiB); sin medición en AWS |
| **Escenarios de caos y reconciliación** | `loadtest/chaos.sh` (consumidor, TimescaleDB, Kafka) sobre el proyecto aislado `fleet-chaos`, y reconciliación final. `ResilientProducer` reemplaza el productor tras un error fatal | `loadtest/chaos.sh kafka` (parámetros en el README) | Reportes `ch-*` en `loadtest/reports/2026-11-07/`. A y B OK (#104). C encontró el defecto del productor (#105) y se corrigió: 905/905 reenvíos confirmados sin reiniciar la API (#114). `test_producer_replacement.py` | Implementado · Dobles · **Real** | Queda la tarea auxiliar de aiokafka, cosmética (`Task was destroyed but it is pending!`, #114). En el cierre la API del stack principal se reconstruyó con la corrección (paso 01), pero el caos no se repitió |
| **Docker Compose y Terraform** | `docker-compose.yml` (perfiles `demo` y `test`). `infra/terraform/`: VPC, EC2 AL2023, IAM con SSM, EBS cifrado con snapshots y alarmas. `infra/deploy/` (override sin puertos internos, Caddy, despliegue por SHA, respaldo y restauración con metadatos) | `docker compose up -d --build --wait`; `infra/scripts/validate.sh all` | Paso 01 de la verificación final. `fmt`, `init`, `validate`, `terraform test` (4/4), TFLint, Trivy, ShellCheck, Compose y puertos, Caddy, instalación en AL2023 y respaldo y restauración simulados (33/33) (#119–#135; paso 10) | Compose: Implementado · **Real**. Terraform: Implementado · Dobles y estática | **Pendiente: plan, despliegue y restauración real en AWS.** Sin cuenta ni credenciales. El workflow `infra` no ha corrido en GitHub. Una sola instancia en una AZ (ver [arquitectura-aws.md](arquitectura-aws.md)) |
| **Auditoría de IA** | [auditoria-ia.md](auditoria-ia.md): entradas numeradas por etapa (decisiones, intervenciones, errores de la IA, verificaciones y pendientes). Casos resumidos en el [README](../README.md#auditoría-de-ia-casos-para-evaluar) | — | El registro completo, con cada entrada ligada a su commit y evidencia | Documentado | Registra solo lo ocurrido. Algunos detalles no quedaron registrados en su momento: por ejemplo, cuál prueba falló en #107 |
| **Video y demostración del entorno agéntico** | [guion-video.md](guion-video.md) (unos 9 min, 2 de ellos para el entorno y la auditoría), [entorno-agentico.md](entorno-agentico.md) (recorrido de 2 min), [CLAUDE.md](../CLAUDE.md) (agregado en el cierre) y [demo.md](demo.md) | Seguir `demo.md` | Este cierre | Preparado | **Pendiente: grabar el video.** Lo hace el usuario |

## Pendientes visibles

| Pendiente | Estado al cierre | Evidencia o alternativa |
|---|---|---|
| Chat con el proveedor real | **Pendiente.** No hay `.env` ni credencial en este equipo | Con una clave: `AGENT_REAL_PROVIDER_TEST=1 docker compose --profile test run --rm tests tests/acceptance/test_agent_service.py -v`. Sin clave, se muestran las herramientas reales con `fleet-check` y el estado `not_configured` ([demo.md](demo.md#5-agente-con-proveedor-real-si-está-configurado)) |
| Captura y cierre real en Android | **Pendiente.** Sin teléfono ni emulador | `npm run e2e` cubre el núcleo con SQLite y API reales; el recorrido manual está en el README, "Recorrido sin conexión" |
| Ejecución de GitHub Actions y EAS | **Pendiente.** Sin cuenta de Expo y sin push de esta rama | Validación local del orden de pasos (paso 09) y `expo export` (paso 08). Al hacer push se ejecutan `mobile.yml` (`checks`) e `infra.yml` |
| Plan, despliegue y restauración real en AWS | **Pendiente.** Sin cuenta | Validación sin credenciales (paso 10) y respaldo y restauración con simuladores (33/33). Procedimiento en [infra/README.md](../infra/README.md) |
| Tarea auxiliar de aiokafka | **Pendiente** (cosmético) | #114; no afecta la ingesta |
| Capacidad medida y objetivos de rendimiento | **Pendiente.** `load-300` no cumplió | Ver la fila de carga. Alternativa medida: la ingesta por lotes (`batch-100`) sí cumplió |
| Repetir la aceptación con la API reconstruida | **Resuelto en el cierre** | Pasos 01 a 03: API reconstruida con `ResilientProducer`; 236 pasadas y 0 fallidas. Estaba abierto desde #115 |
| `terraform validate` | **Resuelto** el 2026-11-08 (#131) | Repetido en el paso 10 |

## Verificación final (2026-11-08)

- **Código bajo prueba:** commit `1a2b3ae` (la punta de `feat/infraestructura-aws`). Los cambios del cierre que no estaban confirmados eran documentación y el propio script de verificación, sin código de servicios.
- **Entorno:** Windows con Docker Desktop. Docker 29.9.2, Compose v5.6.1, 4 CPU y 3,78 GiB para Docker.
- **Comando:**

  ```bash
  scripts/verificacion_final.sh 2026-11-08
  ```

- **Salidas completas:** [verificacion/2026-11-08/](verificacion/2026-11-08/), un `.log` por paso y [resumen.tsv](verificacion/2026-11-08/resumen.tsv) con el código y la duración.
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
