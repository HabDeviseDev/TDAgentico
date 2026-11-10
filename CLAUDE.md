# Instrucciones del proyecto para Claude Code

> **Incorporado el 2026-11-08, durante el cierre de la entrega (rama `feat/cierre-entrega`).**
> Durante las etapas 1 a 7 el repositorio **no tenía** `CLAUDE.md` ni `AGENTS.md` (ver `docs/auditoria-ia.md`, #20). El contexto se dio en cada solicitud. Este archivo reúne las reglas que se aplicaron en la práctica, para que una sesión nueva trabaje igual. No describe reglas que no se hayan seguido.

## Proyecto

MVP de monitoreo de flotas: ingesta API → Kafka → consumidor → TimescaleDB, portal React con SSE, alertas de detención en zonas, agente LangChain de solo lectura con circuit breaker, app móvil Expo sin conexión e infraestructura AWS en Terraform. Punto de entrada: `README.md`; matriz de cumplimiento: `docs/entrega.md`.

## Forma de trabajo

- **Una etapa por rama**, creada desde la rama anterior con sus correcciones (`feat/ingesta-posiciones` → … → `feat/infraestructura-aws` → `feat/cierre-entrega`). Antes de cambiar código, leer el repositorio y el README.
- **Proponer antes de decisiones abiertas** (contratos, esquema, semántica) y dejar que el usuario decida; las decisiones propias se registran como "Decisión de la IA".
- **Commit solo cuando el usuario lo pide.** Mensaje en español que describa el cambio, con la línea `Co-Authored-By` de Claude. **El push lo hace el usuario** con su cuenta.
- Los defectos que encuentra la revisión del usuario se corrigen en la misma rama, con pruebas de regresión.

## Seguridad y datos

- **No introducir, pedir ni buscar credenciales** (Anthropic, AWS, Expo, Git). Sin credencial, la verificación queda pendiente y se dice.
- **No debilitar TLS** (`--trusted-host`, `verify=False`, `sslVerify=false`, etc.). Si una descarga falla por certificados, buscar otra vía con verificación o dejarlo pendiente.
- **No borrar datos ni volúmenes sin preguntar:** nunca `docker compose down -v`. Para liberar recursos, `docker compose stop`.
- Secretos fuera de Terraform, `user_data`, salidas, registros y archivos versionados (`.env` está en `.gitignore`).

## Pruebas y verificación

- **Todo corre en Docker**: el `pip` del host falla por la inspección TLS de la red y el Node del host (16) es antiguo.
  - Backend: `docker compose --profile test run --rm tests -v` (unitarias + aceptación con el stack levantado); solo unitarias: `docker compose --profile test run --rm --no-deps tests tests/unit -v`.
  - Portal: `docker build --no-cache --target test services/portal`.
  - App: `node:22-bookworm-slim` con `node_modules` en el volumen `fleet-mobile-modules` (comandos en el README, "Pruebas de la app").
  - Infraestructura: `infra/scripts/validate.sh all` (sin credenciales).
  - Todo junto, con salidas guardadas: `scripts/verificacion_final.sh`.
- En Git Bash usar `MSYS_NO_PATHCONV=1` con `docker run -v/-w`.
- **Distinguir siempre** implementación, prueba con dobles y verificación real. No presentar una respuesta preparada del chat como respuesta del modelo.
- **Conservar los fallos observados** (ejecuciones de carga que no cumplieron, pruebas intermitentes) con su evidencia; no repetir hasta que pase sin decirlo.
- El equipo es lento (i3, 8 GB): con el stack levantado, Kafka consume mucha CPU. Correr pasos largos en segundo plano y detener el stack si satura.

## Edición de archivos

- Scripts con finales de línea LF (`.gitattributes`); los ejecutables con `git add --chmod=+x`.
- **No generar código con heredocs de shell ni con cadenas de Python que contengan escapes** (`\n`, `\1`, `\` de continuación): ya rompieron archivos dos veces (auditoría #43 y #133). Usar edición directa o escribir el archivo completo.
- Después de editar scripts: `bash -n` y ShellCheck (`validate.sh shell`).

## Documentación y auditoría

- README y documentos en español, con **comandos comprobados** contra el código actual (servicios, perfiles, puertos).
- `docs/auditoria-ia.md` registra **solo lo que ocurrió**: decisiones de la IA, intervenciones, errores de la IA y cómo se corrigieron, verificaciones con su resultado, pendientes. Numeración continua; no reescribir entradas anteriores.
- Sin precios ni cifras inventadas; los objetivos no medidos se presentan como pendientes.
