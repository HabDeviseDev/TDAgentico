"""Pruebas controladas del simulador con un servidor HTTP local de prueba."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import simulate


class Recorder(BaseHTTPRequestHandler):
    responses: list[int] = []
    bodies: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        type(self).bodies.append(json.loads(self.rfile.read(length)))
        code = type(self).responses.pop(0) if type(self).responses else 202
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Recorder.responses, Recorder.bodies = [], []
    httpd = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_reintento_reutiliza_el_mismo_event_id(server):
    Recorder.responses = [503, 503, 202]
    event = {"event_id": "fijo", "vehicle_id": "BOG-001"}
    ok, attempts = simulate.post_position(server, event, retries=3, timeout_s=2)
    assert ok and attempts == 2
    assert [b["event_id"] for b in Recorder.bodies] == ["fijo", "fijo", "fijo"]


def test_422_no_se_reintenta(server):
    Recorder.responses = [422]
    ok, _ = simulate.post_position(server, {"event_id": "x", "vehicle_id": "BOG-001"},
                                   retries=3, timeout_s=2)
    assert not ok
    assert len(Recorder.bodies) == 1


def test_simulacion_envia_ids_estables_event_id_nuevos_y_fechas_utc(server):
    code = simulate.main(["--url", server, "--vehicles", "3", "--interval", "0.2",
                          "--duration", "0.5", "--seed", "7"])
    assert code == 0
    bodies = Recorder.bodies
    assert {b["vehicle_id"] for b in bodies} == {"BOG-001", "BOG-002", "BOG-003"}
    assert len({b["event_id"] for b in bodies}) == len(bodies)  # uno nuevo por posición
    assert all(b["captured_at"].endswith("Z") for b in bodies)
    # Recorridos alrededor de Bogotá.
    assert all(4.4 < b["lat"] < 4.9 and -74.3 < b["lon"] < -73.9 for b in bodies)


def test_recorridos_reproducibles_con_la_misma_semilla():
    import random

    a = simulate.build_fleet(4, random.Random(1))
    b = simulate.build_fleet(4, random.Random(1))
    assert [(v.vehicle_id, v.lat, v.lon) for v in a] == [(v.vehicle_id, v.lat, v.lon) for v in b]
