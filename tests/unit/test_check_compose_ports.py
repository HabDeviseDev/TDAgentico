"""Reglas de infra/scripts/check_compose_ports.py sobre configuraciones fijas
(sin Docker): el verificador debe rechazar lo que el override debe quitar."""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "check_compose_ports.py"
if not _PATH.exists():  # la imagen de pruebas no copia infra/
    pytest.skip("infra/ no está disponible", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("check_compose_ports", _PATH)
ccp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccp)


def _config(**overrides):
    services = {
        "kafka": {"environment": {"KAFKA_LISTENERS": "PLAINTEXT://:9092,CONTROLLER://:9093"}},
        "timescaledb": {"environment": {"POSTGRES_PASSWORD": "otra"}},
        "api": {"environment": {"DATABASE_URL": "postgresql://fleet:otra@timescaledb:5432/fleet"}},
        "caddy": {"ports": [{"target": 80, "published": "80"}, {"target": 443, "published": "443"}]},
    }
    for name, svc in overrides.items():
        services[name] = {**services.get(name, {}), **svc}
    volumes = {v: {"driver_opts": {"device": f"/data/{v}"}} for v in ("kafka-data", "tsdb-data")}
    return {"services": services, "volumes": volumes}


def test_configuracion_valida():
    assert ccp.check(_config()) == []


def test_rechaza_puerto_interno_heredado():
    errors = ccp.check(_config(timescaledb={"ports": [{"target": 5432, "published": "5432"}]}))
    assert any("timescaledb" in e for e in errors)


def test_rechaza_puerto_expuesto_sin_publicar_fuera_de_caddy():
    errors = ccp.check(_config(api={"ports": [{"target": 8000}]}))
    assert any("api" in e for e in errors)


def test_rechaza_puerto_extra_en_caddy():
    caddy = {"ports": [{"target": 80, "published": "80"}, {"target": 2019, "published": "2019"}]}
    assert any("caddy" in e for e in ccp.check(_config(caddy=caddy)))


def test_rechaza_listener_externo_de_kafka():
    kafka = {"environment": {"KAFKA_LISTENERS": "PLAINTEXT://:9092,EXTERNAL://:9094"}}
    assert any("EXTERNAL" in e for e in ccp.check(_config(kafka=kafka)))


def test_rechaza_credenciales_locales():
    api = {"environment": {"DATABASE_URL": "postgresql://fleet:fleet@timescaledb:5432/fleet"}}
    assert any("fleet:fleet" in e for e in ccp.check(_config(api=api)))
    tsdb = {"environment": {"POSTGRES_PASSWORD": "fleet"}}
    assert any("POSTGRES_PASSWORD" in e for e in ccp.check(_config(timescaledb=tsdb)))


def test_rechaza_volumen_fuera_de_data():
    config = _config()
    config["volumes"]["tsdb-data"] = {}
    assert any("tsdb-data" in e for e in ccp.check(config))
