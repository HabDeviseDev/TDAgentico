#!/usr/bin/env python3
"""Comprueba el orden efectivo de los pasos de los workflows de GitHub Actions.

Para cada job, ningún paso `run` puede ejecutarse en un directorio del
repositorio (por `defaults.run.working-directory` del workflow o del job, o
por su propio `working-directory`) antes de `actions/checkout`.

Uso: python scripts/check_workflow_order.py .github/workflows/*.yml
Requiere PyYAML.
"""

from __future__ import annotations

import sys

import yaml


def working_dir(step: dict, job: dict, workflow: dict) -> str | None:
    if "working-directory" in step:
        return step["working-directory"]
    for scope in (job, workflow):
        wd = (scope.get("defaults") or {}).get("run", {}).get("working-directory")
        if wd:
            return wd
    return None


def check(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        workflow = yaml.safe_load(fh)
    problems = []
    for name, job in (workflow.get("jobs") or {}).items():
        checked_out = False
        for i, step in enumerate(job.get("steps") or []):
            uses = str(step.get("uses", ""))
            if uses.startswith("actions/checkout@"):
                checked_out = True
                continue
            if "run" in step:
                wd = working_dir(step, job, workflow)
                if wd and wd not in (".", "./") and not checked_out:
                    label = step.get("name") or step["run"].splitlines()[0]
                    problems.append(f"{path}: job '{name}', paso {i + 1} ('{label}') usa "
                                    f"'{wd}' antes de actions/checkout")
        print(f"{path}: job '{name}': " + " -> ".join(
            s.get("name") or s.get("uses") or s["run"].splitlines()[0] for s in job.get("steps") or []))
    return problems


if __name__ == "__main__":
    found = [p for path in sys.argv[1:] for p in check(path)]
    for p in found:
        print("ERROR:", p)
    sys.exit(1 if found else 0)
