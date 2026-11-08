"""Pruebas de fleet-backup y fleet-restore con AWS CLI y Docker Compose simulados.

Los scripts reales (infra/deploy/bin) se ejecutan con bash. `docker` y `aws`
son sustitutos en el PATH que registran cada llamada en un archivo y emulan
pg_dump, pg_restore, psql, las herramientas de Kafka y S3 (un directorio
local). Así se verifica qué archivos se suben, qué valida la fase previa y
que no haya acciones destructivas cuando esa validación falla.

Requiere bash, jq, sha256sum y stat de GNU/busybox (Linux, CI o contenedor).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "infra" / "deploy" / "bin"
LIB = ROOT / "infra" / "deploy" / "lib.sh"
BUCKET = "fleet-demo-backups"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not all(shutil.which(t) for t in ("bash", "jq", "sha256sum")),
    reason="requiere bash, jq y sha256sum en Linux (ver infra/scripts/validate.sh backup)",
)

# Marcas de acciones que modifican servicios, la base o los offsets.
DESTRUCTIVE = (
    " stop api",
    "DROP DATABASE",
    "CREATE DATABASE",
    "timescaledb_pre_restore",
    "pg_restore -U fleet -d fleet",
    "--reset-offsets",
    " up -d",
    "run --rm",
)

DOCKER_STUB = r"""#!/bin/bash
echo "docker $*" >> "$FAKE_LOG"
[ "$1" = compose ] || exit 0
shift
while [ $# -gt 0 ]; do
  case "$1" in --project-directory | -f | --env-file) shift 2 ;; *) break ;; esac
done
cmd=$1; shift
[ "$cmd" = exec ] || exit 0
[ "$1" = -T ] && shift
svc=$1; shift
kafka_up() { [ ! -e "$FAKE_DIR/kafka-down" ] || { echo "kafka no disponible" >&2; exit 1; }; }
case "$svc $*" in
  "timescaledb pg_dump"*) printf 'PGDMP-volcado-ficticio' ;;
  "timescaledb pg_restore --list"*)
    head -c 5 | grep -q PGDMP || { echo "pg_restore: error: input file does not appear to be a valid archive" >&2; exit 1; } ;;
  "timescaledb pg_restore"*) cat > "$FAKE_DIR/restored.dump" ;;
  "timescaledb psql"*) ;;
  "kafka /opt/kafka/bin/kafka-consumer-groups.sh"*) kafka_up; cat "$FAKE_DIR/describe.txt" ;;
  "kafka /opt/kafka/bin/kafka-topics.sh"*)
    kafka_up
    for p in 0 1 2; do printf '\tTopic: fleet.positions.v1\tPartition: %s\tLeader: 1\tReplicas: 1\tIsr: 1\n' "$p"; done ;;
  "kafka /opt/kafka/bin/kafka-get-offsets.sh"*) kafka_up; cat "$FAKE_DIR/earliest.txt" ;;
  "kafka sh -c"*) kafka_up; cat > "$FAKE_DIR/replay.csv" ;;
  *) echo "docker simulado: llamada no esperada: $svc $*" >&2; exit 99 ;;
esac
"""

AWS_STUB = r"""#!/bin/bash
echo "aws $*" >> "$FAKE_LOG"
pos=()
while [ $# -gt 0 ]; do
  case "$1" in --region) shift 2 ;; --*) shift ;; *) pos+=("$1"); shift ;; esac
done
[ "${pos[0]} ${pos[1]}" = "s3 cp" ] || { echo "aws simulado: no soportado: ${pos[*]}" >&2; exit 99; }
map() { case "$1" in s3://*) echo "$FAKE_S3/${1#s3://}" ;; *) echo "$1" ;; esac; }
src=$(map "${pos[2]}"); dst=$(map "${pos[3]}")
[ -f "$src" ] || { echo "fatal error: An error occurred (404) when calling the HeadObject operation: Not Found" >&2; exit 1; }
mkdir -p "$(dirname "$dst")"
cp "$src" "$dst"
"""

DESCRIBE = """\
Consumer group 'fleet-ingestion' has no active members.

GROUP           TOPIC              PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG  CONSUMER-ID HOST CLIENT-ID
fleet-ingestion fleet.positions.v1 2          5               9               4    -           -    -
fleet-ingestion fleet.positions.v1 0          10              12              2    -           -    -
fleet-ingestion fleet.positions.v1 1          20              20              0    -           -    -
"""


class Env:
    def __init__(self, tmp: Path, bucket: str = BUCKET) -> None:
        self.tmp = tmp
        self.fake = tmp / "fake"
        self.data = tmp / "data"
        self.s3 = tmp / "s3"
        self.log = tmp / "calls.log"
        stub_bin = tmp / "stubbin"
        etc = tmp / "etc"
        for d in (self.fake, self.data, self.s3, stub_bin, etc):
            d.mkdir(parents=True)
        for name, body in (("docker", DOCKER_STUB), ("aws", AWS_STUB)):
            stub = stub_bin / name
            stub.write_text(body, newline="\n")
            stub.chmod(0o755)
        (etc / "deploy.env").write_text(
            f"FLEET_REGION='us-east-1'\nFLEET_BACKUP_BUCKET='{bucket}'\nFLEET_BACKUP_DAYS='30'\n",
            newline="\n",
        )
        (self.fake / "describe.txt").write_text(DESCRIBE)
        (self.fake / "earliest.txt").write_text(
            "fleet.positions.v1:0:0\nfleet.positions.v1:1:0\nfleet.positions.v1:2:0\n"
        )
        self.log.touch()
        self.env = {
            **os.environ,
            "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
            "FLEET_LIB": str(LIB),
            "FLEET_ETC": str(etc),
            "FLEET_STATE": str(tmp / "state"),
            "FLEET_DATA_DIR": str(self.data),
            "FLEET_CURRENT": str(tmp / "current"),
            "FAKE_LOG": str(self.log),
            "FAKE_DIR": str(self.fake),
            "FAKE_S3": str(self.s3),
        }

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(BIN / script), *args],
            env=self.env, capture_output=True, text=True, timeout=60,
        )

    def calls(self) -> str:
        return self.log.read_text()

    def reset_log(self) -> None:
        self.log.write_text("")

    def backup(self) -> str:
        """Ejecuta fleet-backup y devuelve el identificador del respaldo."""
        r = self.run("fleet-backup")
        assert r.returncode == 0, r.stderr + r.stdout
        dumps = sorted((self.data / "backups").glob("fleet-*.dump"))
        assert len(dumps) == 1
        return dumps[0].name.removesuffix(".dump")

    def s3_object(self, name: str) -> Path:
        return self.s3 / BUCKET / "postgres" / name

    def local(self, name: str) -> Path:
        return self.data / "backups" / name

    def kafka_down(self) -> None:
        (self.fake / "kafka-down").touch()


def assert_nothing_destructive(env: Env) -> None:
    calls = env.calls()
    found = [m for m in DESTRUCTIVE if m in calls]
    assert not found, f"acciones destructivas tras una validación fallida: {found}\n{calls}"


def assert_preflight_failure(r: subprocess.CompletedProcess[str], env: Env, fragment: str) -> None:
    assert r.returncode == 1, r.stdout + r.stderr
    assert fragment in r.stderr, r.stderr
    assert "No se detuvo ningún servicio ni se modificó la base." in r.stderr
    assert_nothing_destructive(env)


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# --- fleet-backup ----------------------------------------------------------


def test_backup_sube_dump_y_metadatos_del_mismo_respaldo(env: Env) -> None:
    backup_id = env.backup()

    dump = env.s3_object(f"{backup_id}.dump")
    meta_file = env.s3_object(f"{backup_id}.meta.json")
    assert dump.read_bytes() == env.local(f"{backup_id}.dump").read_bytes()
    assert meta_file.read_bytes() == env.local(f"{backup_id}.meta.json").read_bytes()

    meta = json.loads(meta_file.read_text())
    assert meta["format"] == 1
    assert meta["backup_id"] == backup_id
    assert meta["dump_file"] == f"{backup_id}.dump"
    assert meta["dump_bytes"] == dump.stat().st_size
    sha = subprocess.run(["sha256sum", str(dump)], capture_output=True, text=True).stdout.split()[0]
    assert meta["dump_sha256"] == sha
    assert meta["kafka"] == {
        "group": "fleet-ingestion",
        "topic": "fleet.positions.v1",
        "captured": "before_dump",
        "offsets": [
            {"partition": 0, "offset": 10},
            {"partition": 1, "offset": 20},
            {"partition": 2, "offset": 5},
        ],
    }

    calls = env.calls().splitlines()
    describe = next(i for i, c in enumerate(calls) if "kafka-consumer-groups.sh" in c and "--describe" in c)
    pg_dump = next(i for i, c in enumerate(calls) if "pg_dump" in c)
    uploads = [c for c in calls if c.startswith("aws s3 cp")]
    assert describe < pg_dump, "los offsets se leen antes del dump"
    assert len(uploads) == 2
    assert uploads[0].endswith(f"s3://{BUCKET}/postgres/{backup_id}.dump")
    assert uploads[1].endswith(f"s3://{BUCKET}/postgres/{backup_id}.meta.json")


def test_backup_sin_kafka_guarda_metadatos_sin_offsets(env: Env) -> None:
    env.kafka_down()
    r = env.run("fleet-backup")
    assert r.returncode == 0, r.stderr
    assert "no admitirá --replay" in r.stdout
    meta = json.loads(next(env.s3.rglob("*.meta.json")).read_text())
    assert meta["kafka"] is None
    assert len(list(env.s3.rglob("*.dump"))) == 1


def test_backup_con_particion_sin_offset_confirmado_no_guarda_offsets(env: Env) -> None:
    (env.fake / "describe.txt").write_text(DESCRIBE.replace("1          20 ", "1          -  "))
    env.backup()
    meta = json.loads(next(env.data.rglob("*.meta.json")).read_text())
    assert meta["kafka"] is None


def test_backup_sin_bucket_no_llama_a_s3(tmp_path: Path) -> None:
    env = Env(tmp_path, bucket="")
    env.backup()
    assert "aws " not in env.calls()
    assert len(list((env.data / "backups").glob("*.meta.json"))) == 1


# --- fleet-restore: caminos completos --------------------------------------


def test_restore_desde_s3_con_replay(env: Env) -> None:
    backup_id = env.backup()
    shutil.rmtree(env.data / "backups")
    env.reset_log()

    r = env.run("fleet-restore", f"s3://{BUCKET}/postgres/{backup_id}.dump", "--yes", "--replay")
    assert r.returncode == 0, r.stdout + r.stderr

    assert (env.fake / "replay.csv").read_text().splitlines() == [
        "fleet.positions.v1,0,10",
        "fleet.positions.v1,1,20",
        "fleet.positions.v1,2,5",
    ]
    assert (env.fake / "restored.dump").read_bytes() == env.s3_object(f"{backup_id}.dump").read_bytes()
    calls = env.calls()
    order = [calls.index(m) for m in (" stop api", "DROP DATABASE", "pg_restore -U fleet -d fleet",
                                      "--reset-offsets", "run --rm", " up -d")]
    assert order == sorted(order), calls
    assert "sh fleet-ingestion" in calls, "el grupo se pasa como argumento al contenedor de Kafka"


def test_restore_local_sin_replay_no_mueve_offsets(env: Env) -> None:
    backup_id = env.backup()
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--no-replay")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = env.calls()
    assert "DROP DATABASE" in calls and "pg_restore -U fleet -d fleet" in calls
    assert "--reset-offsets" not in calls
    assert "kafka" not in calls, "sin replay no se consulta Kafka"
    assert not (env.fake / "replay.csv").exists()
    assert "metadatos válidos" in r.stdout


def test_restore_sin_replay_admite_falta_de_metadatos(env: Env) -> None:
    backup_id = env.backup()
    env.s3_object(f"{backup_id}.meta.json").unlink()
    env.reset_log()

    r = env.run("fleet-restore", f"s3://{BUCKET}/postgres/{backup_id}.dump", "--yes", "--no-replay")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "AVISO sin metadatos" in r.stdout
    assert "--reset-offsets" not in env.calls()


def test_restore_avisa_si_la_retencion_borro_eventos(env: Env) -> None:
    backup_id = env.backup()
    (env.fake / "earliest.txt").write_text(
        "fleet.positions.v1:0:15\nfleet.positions.v1:1:0\nfleet.positions.v1:2:0\n"
    )
    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--replay")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "partición 0: los offsets 10..14 ya no están en Kafka" in r.stdout


# --- fleet-restore: validación previa sin acciones destructivas ------------


def test_replay_sin_metadatos_falla_antes_de_tocar_nada(env: Env) -> None:
    backup_id = env.backup()
    env.s3_object(f"{backup_id}.meta.json").unlink()
    env.reset_log()

    r = env.run("fleet-restore", f"s3://{BUCKET}/postgres/{backup_id}.dump", "--yes", "--replay")
    assert_preflight_failure(r, env, "--replay requiere")


@pytest.mark.parametrize("mode", ["--replay", "--no-replay"])
def test_metadatos_no_json_fallan_en_ambos_modos(env: Env, mode: str) -> None:
    backup_id = env.backup()
    env.local(f"{backup_id}.meta.json").write_text("{no es json")
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", mode)
    assert_preflight_failure(r, env, "no es JSON válido")


@pytest.mark.parametrize("mode", ["--replay", "--no-replay"])
def test_metadatos_de_otro_respaldo_fallan(env: Env, mode: str) -> None:
    backup_id = env.backup()
    meta_path = env.local(f"{backup_id}.meta.json")
    meta = json.loads(meta_path.read_text())
    meta["backup_id"] = "fleet-20200101T000000Z"
    meta["dump_file"] = "fleet-20200101T000000Z.dump"
    meta_path.write_text(json.dumps(meta))
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", mode)
    assert_preflight_failure(r, env, "pertenece a otro respaldo")


@pytest.mark.parametrize("mode", ["--replay", "--no-replay"])
def test_dump_alterado_no_coincide_con_su_checksum(env: Env, mode: str) -> None:
    backup_id = env.backup()
    dump = env.s3_object(f"{backup_id}.dump")
    dump.write_bytes(dump.read_bytes().replace(b"ficticio", b"FICTICIO"))  # mismo tamaño
    env.reset_log()

    r = env.run("fleet-restore", f"s3://{BUCKET}/postgres/{backup_id}.dump", "--yes", mode)
    assert_preflight_failure(r, env, "SHA-256")


def test_dump_truncado_no_coincide_en_tamano(env: Env) -> None:
    backup_id = env.backup()
    dump = env.local(f"{backup_id}.dump")
    dump.write_bytes(dump.read_bytes()[:-3])
    env.reset_log()

    r = env.run("fleet-restore", str(dump), "--yes", "--no-replay")
    assert_preflight_failure(r, env, "tamaño")


def test_replay_con_metadatos_sin_offsets_falla(env: Env) -> None:
    env.kafka_down()
    backup_id = env.backup()
    (env.fake / "kafka-down").unlink()
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--replay")
    assert_preflight_failure(r, env, "no tiene offsets válidos")


@pytest.mark.parametrize(
    "offsets",
    [
        [],
        [{"partition": 0, "offset": -1}],
        [{"partition": "0", "offset": 1}],
        [{"partition": 0, "offset": 1.5}],
        [{"partition": 0, "offset": 1}, {"partition": 0, "offset": 2}],
    ],
    ids=["vacios", "negativo", "texto", "decimal", "particion-repetida"],
)
def test_replay_con_offsets_invalidos_falla(env: Env, offsets: list[dict]) -> None:
    backup_id = env.backup()
    meta_path = env.local(f"{backup_id}.meta.json")
    meta = json.loads(meta_path.read_text())
    meta["kafka"]["offsets"] = offsets
    meta_path.write_text(json.dumps(meta))
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--replay")
    assert_preflight_failure(r, env, "no tiene offsets válidos")


def test_replay_con_grupo_distinto_falla(env: Env) -> None:
    backup_id = env.backup()
    meta_path = env.local(f"{backup_id}.meta.json")
    meta = json.loads(meta_path.read_text())
    meta["kafka"]["group"] = "otro-grupo"
    meta_path.write_text(json.dumps(meta))
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--replay")
    assert_preflight_failure(r, env, "no tiene offsets válidos")


def test_replay_con_particion_inexistente_falla(env: Env) -> None:
    backup_id = env.backup()
    meta_path = env.local(f"{backup_id}.meta.json")
    meta = json.loads(meta_path.read_text())
    meta["kafka"]["offsets"].append({"partition": 7, "offset": 1})
    meta_path.write_text(json.dumps(meta))
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--replay")
    assert_preflight_failure(r, env, "la partición 7")


def test_replay_con_kafka_caido_falla(env: Env) -> None:
    backup_id = env.backup()
    env.kafka_down()
    env.reset_log()

    r = env.run("fleet-restore", str(env.local(f"{backup_id}.dump")), "--yes", "--replay")
    assert_preflight_failure(r, env, "Kafka no responde")


def test_dump_que_pg_restore_no_reconoce_falla(env: Env) -> None:
    backup_id = env.backup()
    dump = env.local(f"{backup_id}.dump")
    dump.write_bytes(b"XXXXX" + dump.read_bytes()[5:])
    meta_path = env.local(f"{backup_id}.meta.json")
    meta = json.loads(meta_path.read_text())
    meta["dump_sha256"] = subprocess.run(
        ["sha256sum", str(dump)], capture_output=True, text=True
    ).stdout.split()[0]
    meta_path.write_text(json.dumps(meta))
    env.reset_log()

    r = env.run("fleet-restore", str(dump), "--yes", "--no-replay")
    assert_preflight_failure(r, env, "no es un archivo válido de pg_restore")


def test_dump_s3_inexistente_falla(env: Env) -> None:
    r = env.run("fleet-restore", f"s3://{BUCKET}/postgres/fleet-20261108T023000Z.dump", "--yes", "--no-replay")
    assert_preflight_failure(r, env, "no se pudo descargar")


@pytest.mark.parametrize(
    "args",
    [
        ["fleet-20261108T023000Z.dump", "--yes"],
        ["fleet-20261108T023000Z.dump", "--replay"],
        ["fleet-20261108T023000Z.dump", "--yes", "--replay", "--no-replay"],
        ["--yes", "--replay"],
        ["fleet-20261108T023000Z.dump", "--yes", "--replay", "--force"],
    ],
    ids=["sin-modo", "sin-yes", "dos-modos", "sin-origen", "opcion-desconocida"],
)
def test_argumentos_invalidos_no_ejecutan_nada(env: Env, args: list[str]) -> None:
    r = env.run("fleet-restore", *args)
    assert r.returncode == 2, r.stdout + r.stderr
    assert env.calls() == ""


def test_nombre_de_respaldo_inesperado_falla(env: Env) -> None:
    other = env.tmp / "base.dump"
    other.write_bytes(b"PGDMP")
    r = env.run("fleet-restore", str(other), "--yes", "--no-replay")
    assert_preflight_failure(r, env, "nombre de respaldo inesperado")
