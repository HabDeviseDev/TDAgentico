"""Pruebas de scripts/verificacion_final.sh sin Docker.

`docker`, `git` y el validador de infraestructura son sustitutos que
registran cada llamada y fallan (código 7) cuando sus argumentos coinciden
con la expresión FAKE_FAIL. La salida va a una carpeta temporal
(VERIF_OUT_ROOT). Se comprueban los logs, el resumen y el código final.

Requiere bash (Linux, CI o el contenedor de pruebas).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verificacion_final.sh"
DAY = "2026-01-01"
STEPS = [
    "00-entorno",
    "01-stack-build-up",
    "02-api-productor",
    "03-pytest-unitarias-aceptacion",
    "04-demo-alertas",
    "05-movil-e2e",
    "05b-movil-sin-duplicados",
    "06-stack-stop",
    "07-portal-tipos-build-vitest",
    "08-movil-tipos-pruebas-export",
    "09-workflow-orden",
    "10-infra-validate-all",
    "11-pruebas-verificador-puertos",
]

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or not SCRIPT.exists(),
    reason="requiere bash en Linux y scripts/verificacion_final.sh",
)

STUB = r"""#!/bin/bash
echo "{name} $*" >> "$FAKE_LOG"
if [ -n "${{FAKE_FAIL:-}}" ] && printf '%s' "{name} $*" | grep -qE -- "$FAKE_FAIL"; then
  echo "fallo simulado: {name} $*" >&2
  exit 7
fi
echo "salida simulada: {name} $*"
"""


def run(tmp_path: Path, fail: str = "") -> tuple[subprocess.CompletedProcess[str], Path, str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("docker", "git", "validate"):
        stub = stubs / name
        stub.write_text(STUB.format(name=name), newline="\n")
        stub.chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.touch()
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "VERIF_OUT_ROOT": str(tmp_path / "out"),
        "VALIDATE_SH": str(stubs / "validate"),
        "FAKE_LOG": str(calls),
        "FAKE_FAIL": fail,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), DAY], env=env, capture_output=True, text=True, timeout=120
    )
    return result, tmp_path / "out" / DAY, calls.read_text()


def summary_rows(out: Path) -> dict[str, str]:
    lines = (out / "resumen.tsv").read_text().splitlines()
    assert lines[0] == "paso\tcódigo\tsegundos\tcomando"
    rows = [line.split("\t") for line in lines[1:]]
    assert all(len(r) == 4 for r in rows), "una fila por paso, sin comandos partidos"
    return {r[0]: r[1] for r in rows}


def test_todos_los_pasos_correctos(tmp_path: Path) -> None:
    result, out, calls = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Todos los pasos ejecutados terminaron con código 0" in result.stdout
    assert summary_rows(out) == {s: "0" for s in STEPS}
    for s in STEPS:
        log = (out / f"{s}.log").read_text()
        assert log.startswith(f"# {s}\n")
        assert "código: 0" in log
    assert "validate all" in calls
    assert "compose stop" in calls


def test_paso_fallido_seguido_de_otro_correcto(tmp_path: Path) -> None:
    result, out, calls = run(tmp_path, fail="^docker compose up -d --build --wait$")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FALLARON 1 paso(s): 01-stack-build-up" in result.stderr
    rows = summary_rows(out)
    assert rows["01-stack-build-up"] == "7", "se conserva el código del comando"
    assert rows["02-api-productor"] == "0", "el paso siguiente se ejecuta"
    assert set(rows) == set(STEPS), "todos los pasos se ejecutan"
    assert [s for s, rc in rows.items() if rc != "0"] == ["01-stack-build-up"]

    log01 = (out / "01-stack-build-up.log").read_text()
    assert "fallo simulado: docker compose up -d --build --wait" in log01
    assert "código: 7" in log01
    assert "código: 0" in (out / "02-api-productor.log").read_text()
    assert "01-stack-build-up -> código 7" in result.stdout


def test_comando_intermedio_fallido_dentro_de_un_bloque(tmp_path: Path) -> None:
    # 00-entorno: date; git rev-parse; git status; docker version; docker compose
    # version; docker info. Falla "git status" y los comandos posteriores del
    # bloque terminarían bien: el paso debe fallar igual y detenerse ahí.
    result, out, calls = run(tmp_path, fail="^git status")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FALLARON 1 paso(s): 00-entorno" in result.stderr
    rows = summary_rows(out)
    assert rows["00-entorno"] == "7"
    assert all(rc == "0" for s, rc in rows.items() if s != "00-entorno")

    log00 = (out / "00-entorno.log").read_text()
    assert "fallo simulado: git status --short" in log00
    assert "código: 7" in log00
    assert "docker version" not in calls, "el bloque se detiene en el comando fallido"
    assert "salida simulada: docker version" not in log00


def test_varios_fallos_se_acumulan(tmp_path: Path) -> None:
    result, out, _ = run(tmp_path, fail="^docker compose stop$|^validate all$")

    assert result.returncode == 1
    assert "FALLARON 2 paso(s): 06-stack-stop 10-infra-validate-all" in result.stderr
    rows = summary_rows(out)
    assert {s for s, rc in rows.items() if rc != "0"} == {"06-stack-stop", "10-infra-validate-all"}
    assert rows["11-pruebas-verificador-puertos"] == "0"
