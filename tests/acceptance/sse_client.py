"""Cliente SSE mínimo y síncrono para las pruebas (httpx)."""

from __future__ import annotations

import json
import time
from typing import Callable

import httpx


class SSEClient:
    def __init__(self, base_url: str, path: str = "/v1/vehicles/stream", read_timeout: float = 25):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(connect=5, read=read_timeout, write=5, pool=5),
        )
        self._path = path
        self._stream = None
        self.response: httpx.Response | None = None
        self._lines = None

    def __enter__(self) -> "SSEClient":
        self._stream = self._client.stream("GET", self._path,
                                           headers={"Accept": "text/event-stream"})
        self.response = self._stream.__enter__()
        self.response.raise_for_status()
        self._lines = self.response.iter_lines()
        return self

    def __exit__(self, *exc) -> None:
        if self._stream is not None:
            self._stream.__exit__(*exc)
        self._client.close()

    def next_event(self) -> tuple[str, dict]:
        event_type, data = "message", []
        for line in self._lines:
            if line == "":
                if data:
                    return event_type, json.loads("\n".join(data))
                event_type = "message"  # bloque sin datos (p. ej. retry)
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event_type = value
            elif field == "data":
                data.append(value)
        raise EOFError("el servidor cerró el stream")

    def wait_for(self, predicate: Callable[[str, dict], bool], timeout: float = 30) -> tuple[str, dict]:
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            event = self.next_event()
            seen.append(event[0])
            if predicate(*event):
                return event
        raise AssertionError(f"Tiempo agotado; eventos vistos: {seen[-20:]}")

    def next_snapshot(self, timeout: float = 30) -> dict:
        return self.wait_for(lambda t, _d: t == "snapshot", timeout)[1]


def vehicle_in(snapshot: dict, vehicle_id: str) -> dict | None:
    return next((v for v in snapshot["vehicles"] if v["vehicle_id"] == vehicle_id), None)
