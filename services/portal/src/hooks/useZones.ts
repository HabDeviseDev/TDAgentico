import { useEffect, useState } from "react";
import type { Zone, ZonesResponse } from "../api/types";

export interface ZonesState {
  zones: Zone[];
  loaded: boolean;
  error: string | null;
}

const REFRESH_MS = 60_000;
const RETRY_MS = 10_000;

/** Zonas activas desde GET /v1/zones; se refrescan cada minuto. */
export function useZones(url = "/v1/zones"): ZonesState {
  const [state, setState] = useState<ZonesState>({ zones: [], loaded: false, error: null });

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      let next = REFRESH_MS;
      try {
        const resp = await fetch(url, { cache: "no-store" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const body = (await resp.json()) as ZonesResponse;
        if (!disposed) setState({ zones: body.zones, loaded: true, error: null });
      } catch (err) {
        next = RETRY_MS;
        // Se conservan las zonas ya cargadas.
        if (!disposed) setState((s) => ({ ...s, error: String(err) }));
      }
      if (!disposed) timer = setTimeout(load, next);
    }

    load();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [url]);

  return state;
}
