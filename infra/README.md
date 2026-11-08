# Despliegue en AWS

Terraform y scripts para desplegar el MVP en **una instancia EC2 con Docker Compose**. Arquitectura, decisiones, puntos únicos de falla, evolución a alta disponibilidad, RPO/RTO y costos: [docs/arquitectura-aws.md](../docs/arquitectura-aws.md).

> **Estado:** código validado sin credenciales. **Aún no se ejecutó `terraform plan` ni `apply` contra una cuenta real.** Ver [Niveles de verificación](#niveles-de-verificación).

```
infra/
├── terraform/                 # red, instancia, IAM, volumen, snapshots, alarmas
│   ├── templates/             # user_data, instalación de Docker, fleet-deploy
│   ├── terraform.tfvars.example
│   └── backend.tf.example     # estado remoto en S3 (recomendado)
├── deploy/                    # lo que corre en la instancia
│   ├── compose.aws.yml        # override del Compose local
│   ├── Caddyfile              # proxy de entrada (HTTPS)
│   ├── deploy.sh              # despliegue por pasos
│   ├── lib.sh
│   ├── bin/                   # fleet-health, fleet-ops, fleet-backup, fleet-restore
│   └── systemd/               # temporizadores de salud y respaldo
└── scripts/
    ├── validate.sh            # validación sin credenciales (local y CI)
    └── check_compose_ports.py # el override no deja puertos internos publicados
```

## Requisitos

**Cuenta y permisos.** Una cuenta de AWS de carga de trabajo (no la de administración de la organización). Quien ejecute Terraform necesita crear y borrar:

- **EC2:** VPC, subred, tablas de rutas, Internet Gateway, grupos de seguridad, instancias, volúmenes, adjuntos, Elastic IP, y leer tipos de instancia.
- **IAM:** roles, políticas en línea, adjuntar políticas administradas, perfiles de instancia e `iam:PassRole` sobre los roles `fleet-<entorno>-instance` y `fleet-<entorno>-dlm`.
- **SSM:** `ssm:GetParameter` sobre `/aws/service/ami-amazon-linux-latest/*`.
- **Otros:** DLM (políticas de ciclo de vida), CloudWatch (alarmas), SNS (si hay `alarm_email`), S3 (si hay `backup_bucket_enabled`) y KMS (`CreateGrant` y `DescribeKey`, si se usa una clave propia).

Terraform **no lee** los secretos: solo recibe sus ARN.

**Herramientas locales.** Terraform ≥ 1.10, AWS CLI v2 con un perfil o sesión de IAM Identity Center y el plugin de Session Manager (`session-manager-plugin`). Para validar sin cuenta, solo Docker.

**Secretos** (se crean antes, en la misma región):

| Secreto | Contenido | Obligatorio |
|---|---|---|
| PostgreSQL | Texto plano, o JSON `{"password": "..."}`. 16 a 128 caracteres `[A-Za-z0-9_-]` (va dentro de `DATABASE_URL`) | Sí |
| Proveedor del modelo | Texto plano con la clave de Anthropic, o JSON `{"api_key": "..."}` | No (sin él, el chat informa "requiere configuración") |
| Token de Git | Texto plano con un token de solo lectura | Solo si el repositorio es privado |

**Dominio** (para HTTPS): un nombre cuyo registro `A` puedas apuntar a la IP de salida `public_ip`. Ver [HTTPS](#https-android-y-uso-remoto).

## Comandos

Todos se ejecutan desde la raíz del repositorio, en bash.

### 1. Crear los secretos

La contraseña se genera en AWS y se pasa por archivo temporal, no por argumentos:

```bash
export AWS_REGION=us-east-1 ENV=demo
umask 077; tmp=$(mktemp)
aws secretsmanager get-random-password --password-length 32 --exclude-punctuation \
  --require-each-included-type --query RandomPassword --output text > "$tmp"
aws secretsmanager create-secret --name "fleet/$ENV/postgres" \
  --secret-string "file://$tmp" --query ARN --output text
rm -f "$tmp"
```

```bash
read -rsp "Clave de Anthropic: " KEY; echo
printf '%s' "$KEY" | aws secretsmanager create-secret --name "fleet/$ENV/anthropic" \
  --secret-string file:///dev/stdin --query ARN --output text
unset KEY
```

### 2. Preparar

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Editar: region, allowed_cidrs, los ARN de los secretos, git_revision y opcionalmente domain_name y acme_email.
git rev-parse HEAD   # SHA para git_revision (debe estar publicado en el remoto)
# Opcional, estado compartido: cp backend.tf.example backend.tf y ajustar.
terraform init
```

`terraform.tfvars` y `backend.tf` están en `.gitignore`. El lockfile del proveedor (`.terraform.lock.hcl`, `hashicorp/aws` 6.72.0) sí se versiona. Trae los hashes `zh:` de los paquetes de todas las plataformas publicadas y los `h1:` de `linux_amd64` y `windows_amd64`; en otra plataforma, `terraform init` agrega su `h1:`.

### 3. Planificar y aplicar

```bash
terraform plan -out tfplan
terraform apply tfplan
terraform output
```

El primer despliegue corre solo, en el arranque (`auto_deploy = true`). Tarda varios minutos porque instala Docker y compila las imágenes en la instancia.

### 4. Verificar

```bash
INSTANCE=$(terraform output -raw instance_id)
aws ssm start-session --target "$INSTANCE"
```

Dentro de la sesión:

```bash
sudo tail -n 40 /var/log/fleet-bootstrap.log      # arranque e instalación
sudo ls -t /var/log/fleet/ | head -3              # registros de despliegue
sudo fleet-ops status                             # revisión, servicios, disco
```

Desde tu equipo (dentro de `allowed_cidrs`):

```bash
curl -fsS "$(terraform output -raw portal_url)/config.json"
```

Un despliegue correcto termina con `despliegue de <SHA> completo` y deja en la base un evento de prueba del vehículo `SMOKE-DEPLOY`.

## Despliegue reproducible

`fleet-deploy <SHA>` obtiene **ese commit exacto** (`git fetch --depth 1 origin <SHA>`), comprueba que `HEAD` coincida y lo guarda en `/opt/fleet/releases/<SHA>`. Luego ejecuta su `infra/deploy/deploy.sh`:

| Paso | Qué hace |
|---|---|
| 1/9 verificar revisión | El código en disco es el SHA pedido; hay Docker, Compose, jq y AWS CLI |
| 2/9 configuración y secretos | Lee los secretos de Secrets Manager y escribe `/etc/fleet/secrets.env` (`0600`, escritura atómica). Genera `/etc/fleet/compose.env` y valida el Compose renderizado |
| 3/9 almacenamiento persistente | Espera el volumen por su ID, lo formatea **solo si está vacío** (sin sistema de archivos ni particiones), lo agrega a `fstab` por UUID y lo monta en `/data` |
| 4/9 imágenes | `pull` de las imágenes publicadas y `build --pull` de las propias |
| 5/9 datos | Levanta Kafka y TimescaleDB, espera que estén sanos y alinea la contraseña del rol con el secreto |
| 6/9 migraciones y tópicos | `migrate` (por red y con contraseña, así también valida el secreto) y `kafka-init`; ambos son idempotentes |
| 7/9 servicios | `up -d --remove-orphans` y espera a que todos estén sanos |
| 8/9 disponibilidad | Portal y API a través de Caddy. Con dominio, comprueba la redirección y el HTTPS con verificación de certificado; si el certificado aún no existe, solo avisa |
| 9/9 ingesta de prueba | `POST /v1/positions` y espera la fila en `ingested_events` (API → Kafka → consumidor → TimescaleDB) |

Al terminar, `/opt/fleet/current` apunta a la revisión y se instalan las herramientas y los temporizadores de esa revisión.

**Se puede repetir:** no reinicializa volúmenes, no formatea un volumen con datos y las migraciones y tópicos son idempotentes. Un solo despliegue a la vez (`flock`).

**Si falla a mitad**, el error dice el paso, la línea, el registro y el comando para reintentar (ejemplo):

```
ERROR despliegue de 0123456789ab falló en el paso «5/9 datos (Kafka y TimescaleDB)» (línea 162, código 1).
      Registro: /var/log/fleet/deploy-20261108T101500Z-0123456789ab.log
      Reintentar: sudo /opt/fleet/bin/fleet-deploy 0123…
```

El estado queda en `/var/lib/fleet/last_deploy`. `/opt/fleet/current` sigue apuntando a la última revisión correcta.

### Actualizar y volver atrás

1. Publica el commit en el remoto y cambia `git_revision` en `terraform.tfvars`.
2. Ejecuta `terraform apply`. No reemplaza la instancia: `user_data` se ignora tras crearla; solo cambian las salidas.
3. Despliega por SSM:

   ```bash
   eval "$(terraform output -raw deploy_command)"
   ```

   O, en una sesión, `sudo /opt/fleet/bin/fleet-deploy <SHA>`.

**Volver atrás** es desplegar el SHA anterior (`sudo fleet-ops status` muestra la revisión actual y la anterior). Las migraciones solo avanzan: si la revisión nueva agregó una migración, la anterior corre sobre el esquema nuevo. Hoy todas son aditivas e idempotentes; una migración destructiva requeriría restaurar un respaldo.

Mantén `git_revision` al día: si la instancia se reemplaza, despliega esa revisión.

### Reinicio de la instancia

No hace falta redesplegar. `fstab` monta `/data` (por UUID, con `nofail`) y Docker no arranca hasta que esté montado (`RequiresMountsFor=/data`), así Kafka y PostgreSQL nunca arrancan sobre un directorio vacío del disco raíz. Después, Docker levanta los contenedores por su política `unless-stopped`; `kafka-init` y `migrate` no se repiten. Si el volumen no aparece, Docker no arranca y la alarma `services` avisa; el arreglo es volver a adjuntarlo y reiniciar.

## Acceso y secretos

- **Portal:** único punto de entrada, por Caddy. Sin dominio, HTTP en el 80 solo para `allowed_cidrs`. Con dominio, HTTPS en el 443 solo para `allowed_cidrs`.
- **Puertos internos:** Kafka (9092/9093), PostgreSQL (5432), API (8000) y agente (8100) no se publican. El override usa `ports: !reset []` para quitar lo que publica el Compose local, y `check_compose_ports.py` lo comprueba. Kafka pierde además el listener `EXTERNAL`.
- **Sin SSH:** no hay par de claves ni puerto 22.
- **Metadatos:** IMDSv2 obligatorio.

### Session Manager (SSM)

- **La instancia** necesita el rol con `AmazonSSMManagedInstanceCore` (lo crea Terraform) y salida HTTPS hacia los endpoints `ssm`, `ssmmessages` y `ec2messages` de la región. Sale por el Internet Gateway con su IP pública; no hay puertos de entrada.
- **El operador** necesita `ssm:StartSession` sobre la instancia y el documento de sesión, más `ssm:TerminateSession` y `ssm:ResumeSession` sobre sus propias sesiones. Para desplegar, `ssm:SendCommand` sobre `AWS-RunShellScript` y la instancia, y `ssm:GetCommandInvocation`. Conviene acotarlo por la etiqueta `Project=fleet`.
- Las sesiones y comandos quedan en CloudTrail.

### Permisos de la instancia

- **Secrets Manager:** `GetSecretValue` solo sobre los ARN indicados.
- **KMS:** `Decrypt` sobre la clave propia de los secretos, solo a través de Secrets Manager.
- **CloudWatch:** `PutMetricData` solo en el espacio de nombres `Fleet/fleet-<entorno>`.
- **S3:** escribir y leer en el bucket de respaldos (si existe), prefijo `postgres/`.

### Dónde están (y dónde no) los secretos

- **No están en:** variables, estado ni salidas de Terraform; `user_data`; archivos versionados; registros de despliegue. Los scripts nunca imprimen valores y los mensajes de error de formato no los muestran.
- **Sí están en:** Secrets Manager; `/etc/fleet/secrets.env` (root, `0600`); y las variables de entorno de los contenedores que los usan, visibles para root con `docker inspect`.
- **La contraseña de PostgreSQL** se aplica con `ALTER ROLE` enviado por stdin (no aparece en la lista de procesos).

### Rotación

| Secreto | Pasos | Servicios afectados |
|---|---|---|
| PostgreSQL | 1. `aws secretsmanager put-secret-value` con un valor nuevo (mismo formato). 2. `sudo /opt/fleet/bin/fleet-deploy <SHA actual>` | Compose recrea `timescaledb` (cambió su variable; los datos siguen en `/data`), el despliegue ejecuta `ALTER ROLE` y se recrean `api` y `consumer`, porque cambió su `DATABASE_URL`. `migrate` usa la nueva al correr. Corte breve de la ingesta: la API responde `503` y los clientes reenvían |
| Proveedor del modelo | Igual: valor nuevo y redesplegar | Se recrea `agent`; el chat se corta unos segundos |
| Token de Git | Valor nuevo | Solo afecta a la próxima obtención de una revisión |

No hay rotación automática (sería una función Lambda de rotación; ver la propuesta de HA).

## HTTPS, Android y uso remoto

Android bloquea por defecto el tráfico HTTP sin cifrar hacia Internet, así que la app del conductor fuera de la red local necesita HTTPS.

1. Define `domain_name` y `acme_email` en `terraform.tfvars` y aplica.
2. Crea un registro DNS `A` del dominio hacia `terraform output public_ip`. Se recomienda `create_eip = true`, para que la IP no cambie al detener la instancia.
3. Caddy obtiene el certificado de Let's Encrypt con el desafío HTTP-01. Para eso **el puerto 80 queda abierto a 0.0.0.0/0**: Let's Encrypt valida desde direcciones que no publica. En ese puerto Caddy solo responde el desafío y redirige a HTTPS; el portal se sirve únicamente por el 443, limitado a `allowed_cidrs`.
4. El certificado y la cuenta ACME se guardan en `/data/caddy` (sobreviven al reemplazo de la instancia) y Caddy los renueva solo.
5. En la app, la URL base del servidor es `https://<dominio>`: el portal reenvía `/v1/positions/batch` a la API.

Requisitos y límites:

- El dominio debe resolverse públicamente antes del primer despliegue o el certificado tardará; mientras tanto el despliegue termina con un aviso.
- **Los teléfonos deben estar dentro de `allowed_cidrs`.** Con datos móviles las IP cambian: para una prueba de campo hay que ampliar el rango temporalmente o usar una VPN con salida fija.
- Let's Encrypt limita la cantidad de certificados por dominio y semana: evita recrear la instancia y borrar `/data` en bucle.
- Sin dominio, el portal funciona por HTTP en el navegador; la app Android no podrá usarlo sin cambiar su configuración de red.

## Operación

Todo como root en una sesión de SSM (`sudo -i`).

| Necesidad | Comando |
|---|---|
| Resumen: revisión, servicios, disco y memoria | `fleet-ops status` |
| Lag del consumidor por partición (`LAG` debe tender a 0) | `fleet-ops lag` |
| Mensajes enviados a la DLQ (offset final por partición) | `fleet-ops dlq` |
| Ver los últimos mensajes de la DLQ con el motivo en las cabeceras | `fleet-ops dlq-peek 10` |
| Estado del circuit breaker del agente hacia la API | `fleet-ops circuit` |
| Registros de un servicio | `fleet-ops logs api` |
| Cualquier comando de Compose con la configuración de AWS | `fleet-ops compose ps` |
| Respaldo inmediato | `fleet-backup` |

`fleet-ops circuit` devuelve el JSON de `GET /v1/chat/status`. `circuit.state` vale `closed` (normal), `open` (la API falló `CB_FAILURE_THRESHOLD` veces seguidas: el agente responde sin consultarla durante `CB_RECOVERY_S`) o `half_open` (probando de nuevo).

**Monitoreo:**

- **Métricas:** cada minuto, `fleet-health.timer` publica en CloudWatch (`Fleet/fleet-<entorno>`) `ServicesHealthy` (1 si los siete servicios están en ejecución y sanos), `DataDiskUsedPercent`, `RootDiskUsedPercent` y `MemoryUsedPercent`.
- **Alarmas:**
  - Chequeo de sistema, con recuperación automática.
  - Chequeo de instancia.
  - CPU > 85 %.
  - Servicios: también salta si no llegan datos.
  - Disco de datos > 80 % y raíz > 85 %.
  - Memoria > 90 %.
- **Avisos:** van por correo si se define `alarm_email`; hay que confirmar la suscripción.

**Registros:** los de los contenedores rotan (`json-file`, 5 archivos de 10 MB por contenedor). Los de despliegue quedan en `/var/log/fleet/` y el de arranque en `/var/log/fleet-bootstrap.log`.

**Ajustes sin tocar el código:** en `/etc/fleet/tuning.env` se pueden poner variables del Compose (por ejemplo `AGENT_MODEL`, `KAFKA_HEAP_OPTS` o `ALERT_AFTER_S`). Se aplican en el siguiente `fleet-deploy`.

## Respaldo y restauración

**Respaldos automáticos:**

- **Snapshots EBS:** de todo `/data`, según `snapshot_interval_hours` y `snapshot_retain_count`.
- **`pg_dump -Fc`:** diario a las 02:30 UTC, en `/data/backups` y, con `backup_bucket_enabled`, en `s3://<bucket>/postgres/`.

Sus límites de consistencia están en [docs/arquitectura-aws.md](../docs/arquitectura-aws.md#respaldos).

### Restaurar la base desde un respaldo lógico

```bash
sudo fleet-restore /data/backups/fleet-20261108T023000Z.dump --yes --replay
# o desde S3:
sudo fleet-restore s3://<bucket>/postgres/fleet-20261108T023000Z.dump --yes --replay
```

**Qué hace:**

1. Detiene `api`, `consumer` y `agent`.
2. Recrea la base `fleet`.
3. Ejecuta `timescaledb_pre_restore()`, `pg_restore` y `timescaledb_post_restore()`.
4. Con `--replay`, mueve el offset del consumidor al instante del respaldo.
5. Aplica las migraciones y levanta los servicios.

La versión de TimescaleDB debe ser la misma que la del respaldo. **Pruébalo primero en otro entorno: no se ejecutó contra una instancia real.**

### Restaurar desde un snapshot

Procedimiento manual, **sin probar**:

1. Crea un volumen desde el snapshot **en la AZ de la instancia** (`terraform output availability_zone`), gp3 y cifrado.
2. Detén la instancia, desacopla el volumen actual y adjunta el nuevo como `/dev/sdf`.
3. Arranca la instancia y comprueba con `fleet-ops status`.
4. Alinea el estado de Terraform:

   ```bash
   terraform state rm aws_volume_attachment.data aws_ebs_volume.data
   terraform import aws_ebs_volume.data <vol-nuevo>
   terraform import aws_volume_attachment.data /dev/sdf:<vol-nuevo>:<instance-id>
   ```

5. Ejecuta `terraform plan`: no debe proponer reemplazar el volumen. Si difiere el tamaño o las IOPS, ajusta las variables.

Para otra AZ (falla de zona), fija `availability_zone`. La instancia y el volumen se recrean en esa zona; antes del primer arranque hay que importar el volumen restaurado como `aws_ebs_volume.data`.

## Destruir y retención de datos

```bash
cd infra/terraform
terraform plan -destroy
terraform destroy
```

**Qué queda:**

- **El snapshot final del volumen de datos**, si `data_volume_final_snapshot = true` (por defecto).
- **Los snapshots que creó DLM:** borrar la política no los borra.
- **El bucket de respaldos con objetos:** el destroy falla en él, a propósito. Para borrarlo, vacíalo explícitamente (incluidas las versiones) y repite.
- **Los secretos:** no los crea Terraform. Para borrarlos, `aws secretsmanager delete-secret`, que tiene una ventana de recuperación.

**Borrado total de los datos**, irreversible: además de lo anterior, borra los snapshots con la etiqueta `Project=fleet` y `Environment=<entorno>`.

## Niveles de verificación

| Nivel | Qué demuestra | Estado |
|---|---|---|
| Sintáctica y estática | Terraform con formato e inicializado; plantilla de `user_data` renderizada y revisada; scripts sin errores de shell; Compose renderiza; el override no publica puertos internos; Caddyfile válido; instalación de Docker, Compose y Buildx en Amazon Linux 2023 | **Hecha en local**, salvo `terraform validate` (ver abajo). El workflow de CI la ejecuta completa, pero aún no corrió en GitHub |
| Plan con proveedor y cuenta reales | Permisos, cuotas, disponibilidad del tipo de instancia en la región, AMI resuelta, nombres sin conflicto | Pendiente |
| Despliegue real | Arranque, montaje del volumen, lectura de secretos, compilación, HTTPS, ingesta de prueba, métricas y alarmas | Pendiente |
| Pruebas de recuperación | Reinicio, reemplazo de instancia, restauración de snapshot y de `pg_dump`, rotación de secretos, RPO/RTO | Pendiente |

### Validación local

Requiere solo Docker:

```bash
infra/scripts/validate.sh all      # o un objetivo: terraform, user_data, tflint, trivy, shell, compose, caddy, install
```

| Objetivo | Qué ejecuta |
|---|---|
| `terraform` | `fmt -check`, `init -backend=false` con el lockfile en solo lectura, `validate` y luego `user_data` |
| `user_data` | Renderiza la plantilla real con valores ficticios (con y sin `auto_deploy`), sin proveedor de AWS. Comprueba que comprimida no supere los 16 KB de EC2 y pasa el resultado por `bash -n` y `shellcheck` |
| `tflint` | Reglas recomendadas del lenguaje Terraform |
| `trivy` | Errores de configuración HIGH y CRITICAL, con los valores por defecto y con todos los recursos opcionales (`infra/scripts/trivy-all-features.tfvars`). Las excepciones aceptadas están justificadas en `infra/terraform/.trivyignore.yaml` |
| `shell` | `bash -n` y `shellcheck` de los 9 scripts |
| `compose` | `docker compose config` del Compose local más el override (todos los perfiles) y `check_compose_ports.py` |
| `caddy` | `caddy validate`, con y sin dominio |
| `install` | `install-docker.sh` en un contenedor `amazonlinux:2023`: paquetes, checksums de Compose y Buildx, versiones |

El workflow [`.github/workflows/infra.yml`](../.github/workflows/infra.yml) corre lo mismo en cada PR que toque `infra/`, sin credenciales, además de las pruebas de `check_compose_ports.py`.

### Resultados (2026-11-08, en este equipo)

| Comprobación | Resultado |
|---|---|
| `terraform fmt -check -recursive` | OK |
| `terraform init -backend=false` | OK (`hashicorp/aws` 6.72.0) |
| `terraform validate` | **No se pudo completar en este equipo**, por dos causas del entorno (no del código). **Pendiente: que pase en CI o en otra máquina.** <ul><li>En Docker, el proveedor (805 MB) no arrancó en el minuto que Terraform espera: medido, entre 31 s y 855 s solo para `--help`. El equipo tiene un i3-4130T con 8 GB y menos de 1 GB libre.</li><li>Con Terraform nativo de Windows, el proveedor arrancó, pero el canal TLS local con el plugin falló con `certificate signed by unknown authority`, compatible con la inspección TLS de la red.</li></ul> |
| Plantilla de `user_data` | OK: 39 y 37 líneas; 5.608 y 5.568 bytes comprimida en base64 (límite: 16.384); `bash -n` y `shellcheck` sin hallazgos; los scripts incrustados son idénticos a los fuentes y no hay valores secretos |
| `tflint` 0.65.0 | OK (corregido: un `data` sin usar) |
| `trivy config` 0.76.2 | OK con dos excepciones justificadas: salida 443 a Internet y tema SNS sin cifrar. Se eliminó la salida por el puerto 80. Hallazgo MEDIUM no bloqueante: VPC sin Flow Logs |
| `shellcheck` 0.11.0 y `bash -n` | OK, 9 scripts |
| `docker compose config` + `check_compose_ports.py` | OK. Solo `caddy` publica 80/443 (el Compose local publica 9094, 5432, 8000, 8100 y 8088); sin listener `EXTERNAL`; sin `fleet:fleet`; datos en `/data` |
| Pruebas de `check_compose_ports.py` | 7 pasadas |
| Suite unitaria del repositorio | 209 pasadas y 1 fallida (`test_agent_chat.py::test_selecciona_y_ejecuta_la_herramienta_real`). Esa prueba pasó sola y en dos repeticiones de su archivo (22/22). Es intermitente con el equipo saturado y esta etapa no toca el agente |
| `caddy validate` 2.11.9 | OK con `:80`, con dominio y con correo vacío |
| Instalación en `amazonlinux:2023` | OK: Docker 25.0.14 (paquete de AL2023), Compose v5.6.1 y Buildx v0.38.1 con checksum verificado. **No se arrancó el demonio**: falta comprobar en EC2 que Compose v5.6.1 funcione con el motor 25.0.14 |
