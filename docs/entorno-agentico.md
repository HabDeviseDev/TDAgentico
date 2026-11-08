# Entorno agéntico: cómo se construyó con Claude Code

Este documento explica cómo se usó Claude Code (app de escritorio, modelo Claude Opus) para construir el repositorio. Cada afirmación remite a un commit o a una entrada de [auditoria-ia.md](auditoria-ia.md) (`#N`).

## Contexto que recibió Claude

- **Una solicitud por etapa**, escrita por el usuario, con alcance, garantías esperadas y criterios de verificación. Las solicitudes de corrección señalaban defectos concretos de la etapa anterior.
- **El propio repositorio**: en cada etapa Claude leyó el código y el README antes de cambiar nada (#20, #38, #57, #77, #94, #116).
- **Sin archivo de instrucciones** durante las etapas 1 a 7 (#20). [`CLAUDE.md`](../CLAUDE.md) se agregó el 2026-11-08, en el cierre, y resume las reglas que se aplicaron en la práctica.
- **Restricciones del usuario, repetidas en cada etapa**:
  - Commit solo cuando él lo pide, y el push lo hace con su cuenta.
  - Sin credenciales.
  - Sin debilitar TLS.
  - Sin borrar volúmenes.
  - La auditoría registra solo hechos.
- **Guías del entorno**: para el agente, Claude consultó la guía de Claude API incluida en Claude Code (modelo, esfuerzo, fallback; #58).

## División en etapas

Cada etapa se hizo en su propia rama, creada desde la anterior con sus correcciones. El primer commit implementa la etapa; el segundo, cuando lo hay, corrige lo que encontró la revisión.

| Etapa | Rama | Implementación | Corrección tras la revisión |
|---|---|---|---|
| 1. Ingesta | `feat/ingesta-posiciones` | `968db25` | `706ae14`: Kafka persistente, migraciones, conflictos de `event_id`, rutas de error (#10–#19) |
| 2. Portal en tiempo real | `feat/portal-tiempo-real` | `c32f8e9` | `189e30d`: timeout de lectura (#34–#37) |
| 3. Zonas y alertas | `feat/zonas-alertas` | `80ed7f1` | `6aa948f`: muestras posteriores a la evaluación (#51–#56) |
| 4. Agente y chat | `feat/agente-operativo` | `3592f56` | `00b623c`: contratos de respuesta e identificadores exactos (#69–#76) |
| 5. App móvil | `feat/mobile-offline` | `8876572` | `b7cdc62`: orden del workflow y ciclo de vida del GPS (#87–#93) |
| 6. Carga y caos | `feat/carga-caos` | `4ca1f2c` | `ed425c1`: reemplazo del productor Kafka (#105, #108–#115) |
| 7. AWS | `feat/infraestructura-aws` | `6604972` | `1a2b3ae`: opciones nulas en Terraform y metadatos de respaldo (#128–#136) |
| Cierre | `feat/cierre-entrega` | este cierre | — |

Todos los commits llevan el trailer `Co-Authored-By: Claude Opus 5.5`. Puedes comprobarlo con:

```bash
git log --format='%h %s%n   %(trailers:key=Co-Authored-By,valueonly)' 968db25^..HEAD
```

## Cómo se revisaron los cambios

1. **Revisión del usuario.** Tras cada etapa, el usuario revisó el resultado y pidió correcciones concretas. Los errores que esas correcciones atribuyen a la implementación anterior de la IA están registrados como tales: #51, #69, #87, #90 y #128.
2. **Herramientas que encontraron errores de la IA**, todos registrados:
   - ShellCheck, TFLint, Trivy y `caddy validate` (#120).
   - Pruebas con simuladores (#133).
   - `tsc`, Vitest y `expo export` (#83).
   - `check_workflow_order.py` (#89).
3. **Verificación contra el sistema real**, además de las pruebas con dobles:
   - Stack real y navegador (#32, #48, #66, #81).
   - Pruebas de carga con reconciliación (#99–#101).
   - Caos que encontró un defecto real (#105).
4. **Fallos conservados.** No se repitió una prueba hasta que pasara sin dejar constancia. Ejemplos: `load-300` y `short-02` no cumplieron los umbrales (#99, #100); hubo pruebas intermitentes (#36, #107, #126); y el primer `terraform validate` no se completó (#123).

## Cómo se ejecutaron las pruebas

Todo corre en contenedores. En este equipo, el `pip` del host falla por la inspección TLS de la red, y Claude no desactivó la verificación (#3, #4). El Node del host (versión 16) es demasiado antiguo (#26).

| Qué | Dónde | Comando |
|---|---|---|
| Unitarias y aceptación del backend | Servicio `tests` (perfil `test`) contra el stack real | `docker compose --profile test run --rm tests -v` |
| Portal: tipos, build y Vitest | Etapa `test` del Dockerfile | `docker build --no-cache --target test services/portal` |
| App: tipos, pruebas y bundle Android | `node:22-bookworm-slim` | Ver README, "Pruebas de la app" |
| Infraestructura sin credenciales | Imágenes fijas de Terraform, TFLint, Trivy, ShellCheck, Caddy, AL2023 y Alpine | `infra/scripts/validate.sh all` |
| Carga y caos | `grafana/k6:2.3.0` + imagen de pruebas | `loadtest/lt.sh`, `loadtest/chaos.sh` |
| Todo, con salidas guardadas | — | `scripts/verificacion_final.sh` → `docs/verificacion/<fecha>/` |

## Recorrido de dos minutos

Antes de grabar, deja listo lo siguiente:

- El repositorio abierto en la app de Claude Code, con el historial de la sesión.
- Una terminal en la raíz del repositorio.
- `docs/auditoria-ia.md` abierto.
- La carpeta `docs/verificacion/2026-11-08/`.

| Tiempo | Qué mostrar | Qué decir |
|---|---|---|
| 0:00–0:20 | En la app, la solicitud de una etapa: por ejemplo, la de la etapa 6 (carga y caos) o la de corrección de la etapa 1 | "Cada etapa empezó con una solicitud escrita con alcance, garantías y criterios de verificación. Claude leía el repositorio antes de cambiar nada." |
| 0:20–0:40 | `git log --oneline --graph --all` y la tabla de etapas de este documento | "Una rama por etapa, cada una desde la anterior. El segundo commit de cada etapa corrige lo que encontró mi revisión." |
| 0:40–1:15 | `docs/auditoria-ia.md`: #1 (decisiones aprobadas), #3 (acción bloqueada por debilitar TLS), #105 a #109 (defecto encontrado por el caos y corregido) y #128 (error de la IA) | "La auditoría registra decisiones, errores de la IA y verificaciones, también las que fallaron. Por ejemplo, el caos reveló que la API necesitaba reiniciarse tras caer Kafka." |
| 1:15–1:40 | `docs/verificacion/2026-11-08/resumen.tsv` y un log, como `03-pytest-unitarias-aceptacion.log` | "La verificación final quedó guardada completa: commit, comando, código de salida y duración de cada paso." |
| 1:40–2:00 | `CLAUDE.md` | "Al cerrar, reuní en `CLAUDE.md` las reglas que se aplicaron en la práctica. Se agregó en el cierre, no al principio." |

Si el historial de la sesión ya no está disponible en la app, muestra la solicitud de la etapa reconstruida desde el commit y su entrada de auditoría, y dilo así.
