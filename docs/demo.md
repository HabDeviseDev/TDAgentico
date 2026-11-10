# Demostración reproducible

Comandos exactos y resultado esperado, en el orden de una demostración. Se ejecutan desde la raíz del repositorio, en Git Bash o en una terminal Linux o macOS con Docker. Los puertos son los del `docker-compose.yml` actual:

- Portal: 8088 (`PORTAL_PORT`).
- API: 8000.
- Agente: 8100 (`AGENT_PORT`).
- TimescaleDB: 5432.
- Kafka para el host: 9094.

Ningún paso usa `docker compose down -v`: los volúmenes se conservan.

Lo marcado **(verificado)** se ejecutó en este equipo; la referencia indica dónde quedó la evidencia. Lo marcado **(pendiente)** no se pudo ejecutar; para cada caso se indica una alternativa.

## 1. Arrancar el entorno

```bash
docker compose up -d --build --wait
```

```bash
docker compose ps
```

**Esperado:**

- `kafka`, `timescaledb`, `api`, `agent` y `portal` quedan `running (healthy)`, y `consumer` queda `running`.
- `kafka-init` y `migrate` terminan con `exited (0)`: crean los topics y aplican las migraciones.

La primera construcción descarga imágenes y dependencias. En la verificación final, con parte de las capas ya en caché de etapas anteriores, el comando tardó 323 s en este equipo (i3, 8 GB, 4 CPU para Docker). Una construcción desde cero tardará más; no se midió. **(verificado: paso 01 de [verificacion/2026-11-08/](verificacion/2026-11-08/))**

Abre **http://localhost:8088**. Si la base ya tiene datos de ejecuciones anteriores, aparecen esos vehículos, marcados como desactualizados si su última muestra es antigua.

## 2. Vehículos en el mapa

```bash
docker compose --profile demo up -d simulator
```

**Esperado:** en unos segundos aparecen 8 vehículos alrededor de Bogotá, que se mueven sin recargar la página. Al seleccionar uno, el detalle se actualiza en vivo. **(verificado: #31, #32)**

Ingesta manual de un evento, que también muestra la idempotencia:

```bash
curl -s -X POST http://localhost:8000/v1/positions -H "Content-Type: application/json" -d '{"event_id":"0c6a3f2e-8d41-4b7a-9f3e-2a1b5c7d9e10","vehicle_id":"DEMO-MANUAL","captured_at":"2026-11-08T12:00:00Z","lat":4.65,"lon":-74.06,"speed_kmh":0}'
```

**Esperado:** `{"status":"accepted","event_id":"0c6a3f2e-…"}` con HTTP 202. Si repites el mismo comando, la respuesta es igual y no se duplica la fila:

```bash
docker compose exec timescaledb psql -U fleet -d fleet -c "SELECT count(*) FROM position_history WHERE vehicle_id = 'DEMO-MANUAL'"
```

La cuenta es 1. En el portal, `DEMO-MANUAL` aparece como desactualizado, porque su `captured_at` es fijo.

Para detener el simulador:

```bash
docker compose --profile demo stop simulator
```

## 3. Generar una alerta y corregirla con un evento atrasado

```bash
docker compose --profile demo run --rm demo-alerts --once
```

**Esperado:** el script publica seis escenarios con la etiqueta `DEMO-<HHMM>`, espera la alerta de `…-ATRASADO`, publica un movimiento de hace 12 min y espera a que la alerta se retire. Al final imprime el estado de cada vehículo según `GET /v1/alerts`:

| Vehículo | Resultado |
|---|---|
| `…-ZONA25` | Alerta: detenido 25 min en `DEMO-Z01` |
| `…-FUERA`, `…-MOVIL`, `…-HUECO` | Sin alerta |
| `…-SINDATOS` | Evidencia insuficiente |
| `…-ATRASADO` | Alerta y después retirada por el evento atrasado |

En el portal, el panel de alertas muestra `ZONA25` y la retirada de `ATRASADO` sin recargar. La posición actual de `ATRASADO` no cambia, porque el evento atrasado solo entra al historial. **(verificado: #47 y paso 04 de la verificación final)**

Por API:

```bash
curl -s localhost:8088/v1/alerts
```

Sin `--once`, el script sigue enviando muestras cada 30 s. Se detiene con Ctrl+C.

## 4. Circuit breaker, sin credencial

```bash
docker compose stop api
```

```bash
curl -s localhost:8088/v1/chat/fleet-check
```

```bash
curl -s localhost:8088/v1/chat/fleet-check
```

**Esperado:** la primera consulta tarda unos 7 s (3 intentos con timeout de conexión de 2 s) y abre el circuito. La segunda responde al instante con `"kind": "circuit_open"` y `retry_after_s`.

```bash
docker compose start api
```

Pasados 30 s (`CB_RECOVERY_S`):

```bash
curl -s localhost:8088/v1/chat/status
```

**Esperado:** `"state": "half_open"`. La siguiente consulta a `fleet-check` cierra el circuito, y `docker compose logs agent` muestra `closed -> open`, `open -> half_open` y `half_open -> closed`. **(verificado: #65)**

## 5. Agente con proveedor real, si está configurado

**Sin credencial**, que es el caso de este equipo:

```bash
curl -s localhost:8088/v1/chat/status
```

**Esperado:** `"configured": false`, con el motivo y sin ningún valor de clave. En el portal, el botón **Asistente** abre el panel con "El agente requiere configuración" y el envío deshabilitado. **El mapa, las alertas y la ingesta funcionan igual sin credencial.** **(verificado: #66, `test_agent_service.py`)**

**Con credencial (pendiente: nunca se ejecutó):**

```bash
cp .env.example .env
```

Completa `ANTHROPIC_API_KEY=` en `.env` y luego:

```bash
docker compose up -d agent
```

En el portal, pregunta, por ejemplo, "¿Qué vehículos tienen alertas activas?". **Esperado:** una respuesta con fuentes, evidencias y botones de vehículos que los seleccionan en el mapa. Prueba automática opcional:

```bash
AGENT_REAL_PROVIDER_TEST=1 docker compose --profile test run --rm tests tests/acceptance/test_agent_service.py -v
```

**Alternativa sin credencial: respuesta preparada, que debe presentarse como SIMULACIÓN.** Sirve para mostrar cómo el portal presenta una respuesta, no al agente. Pega esto en la consola del navegador (F12) con el portal abierto:

```js
// SIMULACIÓN: respuesta preparada a mano. No la generó el modelo ni el agente.
const realFetch = window.fetch.bind(window);
const json = (body) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
window.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.endsWith("/v1/chat/status")) {
    return json({ configured: true, problem: null, provider: "simulación", model: "respuesta-preparada",
      conversation: "independent", limits: { max_question_chars: 500, request_timeout_s: 60 } });
  }
  if (url.endsWith("/v1/chat") && init?.method === "POST") {
    return json({ request_id: "simulacion-1", status: "answered",
      answer: "**[SIMULACIÓN: respuesta preparada, no generada por el modelo]**\n\n**BOG-001**\n- **Ubicación:** 4.60000, -74.10000\n- **Velocidad:** 0 km/h\n- **Última lectura:** 09/11/2026 10:26:28 America/Bogota\n\n**Este dato está desactualizado.**",
      provider: "simulación", model: "respuesta-preparada", display_timezone: "America/Bogota", tools: [],
      sources: [{ tool: "consultar_posiciones", source: "GET /v1/vehicles", status: "ok",
        time_utc: "2026-11-09T15:30:34.000000Z", time_local: "09/11/2026 10:30:34 America/Bogota" }],
      evidence: { alerts: [], zones: [], insufficient_evidence: [], vehicles: [{ vehicle_id: "BOG-001",
        captured_at_utc: "2026-11-09T15:26:28.000000Z", captured_at_local: "09/11/2026 10:26:28 America/Bogota",
        lat: 4.6, lon: -74.1, speed_kmh: 0, age_s: 246, stale: true }] },
      vehicle_ids: ["BOG-001"], zone_ids: [],
      warnings: [{ code: "simulation", message: "Respuesta preparada para la demostración: el agente no está configurado.", tool: null }],
      usage: { steps: 0, max_steps: 5, tool_calls: 0, max_tool_calls: 6, duration_ms: 0 } });
  }
  return realFetch(input, init);
};
```

El portal vuelve a consultar el estado cada 30 s. Cuando el envío se habilite, haz una pregunta. Recarga la página para quitar la simulación. La misma técnica se usó en la verificación de la etapa 4 (#66).

## 6. Persistencia y sincronización móvil

Con el stack levantado, ejecuta el núcleo real de la app (cola SQLite y sincronización) contra la API real. Solo se simulan el corte de red y una respuesta perdida:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network fleet_default -e API_URL=http://api:8000 -v "$(pwd -W)/services/mobile:/app" -v fleet-mobile-modules:/app/node_modules -w /app node:22-bookworm-slim sh -c "npm ci --no-audit --no-fund && npm run e2e"
```

En Linux o macOS usa `$(pwd)` en lugar de `$(pwd -W)`.

**Esperado:** la prueba pasa y su registro muestra 3 capturas aceptadas, 5 pendientes que siguen ahí después de cerrar y reabrir la base, una respuesta perdida que deja los pendientes, y 8 aceptadas al reenviar con los mismos `event_id`. Comprueba que no haya duplicados (sustituye el vehículo por el que imprime la prueba, `MOVIL-DEMO-…`):

```bash
docker compose exec timescaledb psql -U fleet -d fleet -c "SELECT vehicle_id, count(*), count(DISTINCT event_id) FROM position_history WHERE vehicle_id LIKE 'MOVIL-DEMO-%' GROUP BY 1"
```

Las dos cuentas coinciden. En la verificación final, `MOVIL-DEMO-742a72` dio 8 filas de historial, 8 `event_id` distintos y 8 en `ingested_events`. **(verificado: #85 y pasos 05 y 05b de la verificación final)**

En un teléfono o emulador Android **(pendiente)**, sigue el README, "Ejecutar en un teléfono o emulador Android" y "Recorrido sin conexión". Alternativa: el e2e de este paso y las 55 pruebas de la app con SQLite real.

## 7. Recuperación de Kafka

**a) Kafka conserva mensajes y offsets al recrear el contenedor.** Los pasos están en el README, "Verificar que Kafka conserva mensajes y offsets al recrearse". Con el consumidor detenido, `docker compose rm -sf kafka` y `docker compose up -d kafka` dejan `CURRENT-OFFSET`, `LOG-END-OFFSET` y `LAG` iguales, y el pendiente se procesa al arrancar el consumidor. **(verificado: #18)**

**b) La API se recupera sola tras reiniciar Kafka.** Evidencia guardada del escenario de caos C con la corrección:

```bash
cat loadtest/reports/2026-11-07/ch-kafka-08001713/api-log-resumen.txt
```

```bash
cat loadtest/reports/2026-11-07/ch-kafka-08001713/reconcile-report.md
```

**Esperado:**

- El log cuenta 10 `OutOfOrderSequenceNumber`, una línea `Productor Kafka reemplazado (reemplazo n.º 1)` y la tarea auxiliar pendiente de aiokafka, que es cosmética.
- La reconciliación da `Resultado: OK`: 1.250 confirmadas, 0 ambiguos y 0 discrepancias.
- Para comparar, la ejecución sin la corrección (`ch-kafka-07033403/`) necesitó `restart api`.

**(verificado: #105 y #114)** Para repetirlo, ver el README, "Caos controlado". Usa un proyecto aislado (`fleet-chaos`) y tarda varios minutos.

## 8. Infraestructura en AWS

No hay recursos creados: se explica con el código y la validación sin credenciales.

```bash
infra/scripts/validate.sh terraform
```

**Esperado:** `fmt -check`, `init`, `Success! The configuration is valid.`, `terraform test` con 4 de 4 y el renderizado de `user_data` en 4 casos, terminando con `OK (terraform)`. En este equipo tarda unos 3 min.

```bash
python infra/scripts/check_compose_ports.py
```

**Esperado:** `OK: solo caddy publica 80/443…`. El override de AWS no expone Kafka, PostgreSQL, la API ni el agente. Si no hay Python en el host, ejecuta `infra/scripts/validate.sh compose`.

Qué mostrar:

- [arquitectura-aws.md](arquitectura-aws.md): el diagrama de una instancia, los puntos únicos de falla, la evolución a HA (implementado frente a propuesto) y RPO/RTO como objetivos sin validar.
- [infra/README.md](../infra/README.md): el despliegue por SHA en 9 pasos y el respaldo y restauración con metadatos.

**Plan, despliegue y restauración real (pendiente):** requieren una cuenta y los secretos en Secrets Manager. Los pasos exactos están en `infra/README.md`, secciones "Comandos" y "Respaldo y restauración". Alternativas ya ejecutadas:

- `validate.sh all`.
- 33 pruebas de respaldo y restauración con AWS CLI y Compose simulados.
- Los formatos de Kafka probados contra un broker real con `--dry-run` (#135).

## Al terminar

```bash
docker compose --profile demo stop
```

```bash
docker compose stop
```

Los datos de demostración (`DEMO-*`, `MOVIL-DEMO-*`, `TEST-*`) quedan en la base. Para empezar de cero, ver el README, "Reiniciar desde cero": ese comando **borra** los volúmenes.
