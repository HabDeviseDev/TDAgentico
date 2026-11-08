# Guion del video (unos 9 minutos)

Dura unos 9 minutos: 7 para el sistema y 2 para el entorno agéntico y la auditoría de IA. Cada bloque indica qué mostrar en pantalla y una guía oral breve, para decir con tus palabras. Los comandos exactos están en [demo.md](demo.md).

## Antes de grabar (unos 15 minutos)

1. `docker compose up -d --build --wait` y `docker compose ps`: todo sano.
2. `docker compose --profile demo up -d simulator`.
3. Ejecuta `docker compose --profile demo run --rm demo-alerts --once` **una vez antes**, para comprobar que funciona. En el video se vuelve a ejecutar.
4. Ten abiertos:
   - El portal (http://localhost:8088).
   - Una terminal en la raíz.
   - `docs/entrega.md`, `docs/auditoria-ia.md`, `loadtest/reports/2026-11-07/`.
   - `docs/verificacion/2026-11-08/resumen.tsv`.
   - La app de Claude Code con el historial de las sesiones.
5. Sin credencial del modelo, decide si vas a mostrar la respuesta preparada del chat. Si la muestras, **di en voz alta que es una simulación**: el texto ya lo indica.

## Bloques

| Tiempo | En pantalla | Guía oral |
|---|---|---|
| **0:00–0:40** Problema y arquitectura | Diagrama del README | "MVP de monitoreo de flotas. Los vehículos envían posiciones a una API que responde 202 solo cuando Kafka confirma (acks=all). Un consumidor las guarda en TimescaleDB en una transacción y después confirma el offset. El portal recibe cambios por SSE; un agente responde preguntas con herramientas de solo lectura." |
| **0:40–2:00** Ingesta y portal en vivo | Portal con el simulador: 8 vehículos moviéndose, seleccionar uno. Luego, en la terminal, el `curl` de `DEMO-MANUAL` dos veces y el `count` en 1 | "Garantía: entrega al menos una vez de punta a punta, y en la base efecto de una sola vez, porque el consumidor deduplica por `event_id`. Si la API responde 503, el cliente reenvía el mismo `event_id` sin riesgo de duplicar. Un evento atrasado entra al historial pero no retrocede la posición visible." |
| **2:00–3:15** Alertas y evento atrasado | `demo-alerts --once` en la terminal y el panel de alertas del portal | "Regla: detenido más de 20 minutos dentro de una zona crítica. Se evalúa en una vista consistente, solo con muestras hasta el instante de evaluación. Aquí ATRASADO tiene alerta, llega un movimiento de hace 12 minutos y la alerta se retira. SINDATOS queda como evidencia insuficiente: no se inventa una alerta sin datos recientes." |
| **3:15–4:15** Agente y circuit breaker | `curl /v1/chat/status` (`configured: false`); el panel "El agente requiere configuración"; `docker compose stop api` y `fleet-check` dos veces (la segunda en 0 ms, `circuit_open`), luego `start api` | "Sin credencial, el resto funciona igual. El circuit breaker protege al agente cuando la API cae: tras 3 fallos abre el circuito y rechaza al instante; a los 30 s prueba de nuevo. **No pude probar el chat con el modelo real: no tuve credencial.** Está probado con un modelo guionado y con las herramientas reales contra la API." Si muestras la respuesta preparada: "Esto es una simulación del formato, no una respuesta del modelo." |
| **4:15–5:00** App móvil sin conexión | Salida del e2e (paso 05 en `docs/verificacion/2026-11-08/`) y la consulta de `count` frente a `count DISTINCT` | "La app guarda cada captura en SQLite antes de enviarla, con su payload fijo. Sin red se acumula; al volver la conexión envía lotes, uno a la vez, y reenvía con los mismos `event_id`. El e2e usa SQLite real y la API real, y simula el corte de red. **No la probé en un teléfono ni en un emulador, ni ejecuté EAS.**" |
| **5:00–6:15** Carga, rendimiento y caos | `loadtest/reports/2026-11-07/README.md` o la tabla del README: `load-300` en rojo, `batch-100` en verde; `ch-kafka-08001713/api-log-resumen.txt` | "**La carga original no cumplió**: 300 vehículos a 50 por segundo dieron p95 de 1,76 s frente a 500 ms, 75 respuestas ambiguas y 109 iteraciones descartadas, en un equipo de 4 CPU. La reconciliación no encontró pérdidas ni duplicados: todo lo ambiguo terminó persistido. Por lotes sí se cumplió. El caos encontró un defecto real: tras reiniciar Kafka, la API quedaba en 503 hasta reiniciarla. Se corrigió reemplazando el productor; en la repetición se confirmaron 905 de 905 reenvíos sin reiniciar la API." |
| **6:15–7:00** AWS | `docs/arquitectura-aws.md` (diagrama, puntos únicos de falla) y el final de `validate.sh terraform` (`Success! The configuration is valid.`) | "Para la demo: una instancia EC2 con Docker Compose, volumen de datos cifrado aparte, snapshots, SSM sin SSH y secretos leídos al desplegar. Tiene puntos únicos de falla, documentados con su evolución a alta disponibilidad. Está validado sin credenciales; **no hice plan, despliegue ni restauración real en AWS**." |
| **7:00–9:00** Entorno agéntico y auditoría | Seguir el [recorrido de dos minutos](entorno-agentico.md#recorrido-de-dos-minutos): solicitud de una etapa, `git log` por ramas, auditoría (#3, #105–#109, #128), `resumen.tsv`, `CLAUDE.md` | "Construí el proyecto con Claude Code, una etapa por rama y con mi revisión entre etapas. La auditoría registra lo que hizo la IA, incluidos sus errores: por ejemplo, Kafka sin persistencia en la primera versión, o un `coalesce` que habría roto Terraform sin opciones. También registra lo que no se pudo verificar. `CLAUDE.md` se agregó al final y resume las reglas que se aplicaron." |

## Cierre (si sobra tiempo, 10 segundos)

"Los pendientes están en `docs/entrega.md`: el chat con el modelo real, Android real, Actions y EAS, el despliegue en AWS, la tarea pendiente de aiokafka y el rendimiento objetivo."

## Comprobaciones antes de entregar el video

- No se ve ninguna credencial: ni el `.env` abierto ni variables en la terminal.
- Cada simulación se presentó como tal.
- Las cifras dichas coinciden con `docs/entrega.md` y los reportes.
