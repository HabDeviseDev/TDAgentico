import NetInfo from "@react-native-community/netinfo";
import * as Crypto from "expo-crypto";
import * as Location from "expo-location";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, type AppStateStatus } from "react-native";
import { normalizeApiUrl, sendBatch } from "../core/api";
import { buildSample, normalizeVehicleId } from "../core/contract";
import type { SqlDb } from "../core/db";
import { migrate } from "../core/migrations";
import { Outbox, type Counts, type OutboxRow } from "../core/outbox";
import { DEFAULT_SETTINGS, SettingsStore, type DriverSettings } from "../core/settings";
import { RouteSimulator } from "../core/simulator";
import { SyncCoordinator, type SyncReport, type SyncTrigger } from "../core/sync";
import { openExpoDb } from "../db/expoDb";
import { APP_CONFIG } from "./config";

export type PermissionState = "unknown" | "granted" | "denied" | "blocked";
export type CaptureState = "stopped" | "capturing" | "paused";

export interface LastFix {
  eventId: string;
  capturedAt: string | null;
  lat: number | null;
  lon: number | null;
  speedKmh: number | null;
  skippedReason: string | null;
  simulated: boolean;
}

export interface DriverState {
  ready: boolean;
  fatalError: string | null;
  settings: DriverSettings;
  permission: PermissionState;
  capture: CaptureState;
  simulated: boolean;
  simulateUnknownSpeed: boolean;
  lastFix: LastFix | null;
  online: boolean | null;
  counts: Counts;
  rejected: OutboxRow[];
  skipped: OutboxRow[];
  syncing: boolean;
  lastSync: SyncReport | null;
  nextAttemptAt: number | null;
  notice: string | null;
}

const EMPTY_COUNTS: Counts = { pending: 0, sending: 0, accepted: 0, rejected: 0, skipped: 0 };

function toOnline(state: { isConnected: boolean | null; isInternetReachable?: boolean | null }): boolean | null {
  if (state.isConnected === false || state.isInternetReachable === false) return false;
  if (state.isConnected === true) return true;
  return null;
}

/**
 * Estado y acciones de la app del conductor. Orden de cada captura:
 * lectura → contrato → SQLite → (después) sincronización.
 */
export function useDriver() {
  const [state, setState] = useState<DriverState>({
    ready: false, fatalError: null, settings: DEFAULT_SETTINGS, permission: "unknown",
    capture: "stopped", simulated: false, simulateUnknownSpeed: false, lastFix: null,
    online: null, counts: EMPTY_COUNTS, rejected: [], skipped: [], syncing: false,
    lastSync: null, nextAttemptAt: null, notice: null,
  });
  const patch = useCallback((p: Partial<DriverState>) => setState((s) => ({ ...s, ...p })), []);

  const db = useRef<SqlDb | null>(null);
  const outbox = useRef<Outbox | null>(null);
  const store = useRef<SettingsStore | null>(null);
  const sync = useRef<SyncCoordinator | null>(null);
  const settingsRef = useRef<DriverSettings>(DEFAULT_SETTINGS);
  const onlineRef = useRef<boolean | null>(null);
  const wantCapture = useRef(false);
  const simulatedRef = useRef(false);
  const unknownSpeedRef = useRef(false);
  const watcher = useRef<Location.LocationSubscription | null>(null);
  const simTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const simulator = useRef<RouteSimulator | null>(null);
  const appState = useRef<AppStateStatus | null | undefined>(AppState.currentState as AppStateStatus | null);

  const refresh = useCallback(async () => {
    const o = outbox.current;
    if (!o) return;
    const [counts, rejected, skipped, nextAttemptAt] = await Promise.all([
      o.counts(), o.list("rejected", APP_CONFIG.listLimit), o.list("skipped", APP_CONFIG.listLimit), o.nextAttemptAt(),
    ]);
    patch({ counts, rejected, skipped, nextAttemptAt });
  }, [patch]);

  const runSync = useCallback(async (trigger: SyncTrigger) => {
    const s = sync.current;
    if (!s) return;
    patch({ syncing: true });
    try {
      const report = await s.requestSync(trigger);
      patch({ lastSync: report });
    } catch (err) {
      // Error recuperable: los datos siguen en SQLite.
      patch({ notice: `No se pudo sincronizar: ${String(err)}` });
    } finally {
      patch({ syncing: s.busy });
      await refresh();
    }
  }, [patch, refresh]);

  // -- inicio: base, migraciones, recuperación y primera sincronización --------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const opened = await openExpoDb();
        await migrate(opened);
        db.current = opened;
        outbox.current = new Outbox(opened, Date.now, Math.random, APP_CONFIG.backoff);
        store.current = new SettingsStore(opened);
        settingsRef.current = await store.current.load();
        sync.current = new SyncCoordinator(
          outbox.current,
          (payloads) => sendBatch(settingsRef.current.apiUrl, payloads, fetch, APP_CONFIG.requestTimeoutMs),
          Date.now,
          APP_CONFIG.sync,
          () => onlineRef.current,
          () => void refresh(),
        );
        const recovered = await sync.current.start();
        const perm = await Location.getForegroundPermissionsAsync();
        if (cancelled) return;
        patch({
          ready: true,
          settings: settingsRef.current,
          permission: perm.granted ? "granted" : perm.canAskAgain ? "unknown" : "blocked",
          notice: recovered ? `${recovered} evento(s) de un envío interrumpido volvieron a pendientes` : null,
        });
        await refresh();
        void runSync("open");
      } catch (err) {
        patch({ fatalError: `No se pudo abrir la base local: ${String(err)}` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [patch, refresh, runSync]);

  // -- conectividad: al recuperarla, sincronizar ---------------------------------------
  useEffect(() => {
    return NetInfo.addEventListener((net) => {
      const online = toOnline(net);
      const was = onlineRef.current;
      onlineRef.current = online;
      patch({ online });
      if (online === true && was !== true && sync.current) void runSync("reconnect");
    });
  }, [patch, runSync]);

  // -- sincronización periódica mientras la app está activa ----------------------------
  useEffect(() => {
    const timer = setInterval(() => {
      if (appState.current === "active") void runSync("periodic");
    }, APP_CONFIG.syncIntervalMs);
    return () => clearInterval(timer);
  }, [runSync]);

  // -- captura ---------------------------------------------------------------------
  const handleFix = useCallback(async (fix: { latitude: number; longitude: number; speedMps: number | null;
                                              timestampMs: number }, simulated: boolean) => {
    const o = outbox.current;
    if (!o) return;
    const decision = buildSample({
      eventId: Crypto.randomUUID(),            // una sola vez, antes de guardar
      vehicleId: settingsRef.current.vehicleId,
      timestampMs: fix.timestampMs,
      latitude: fix.latitude,
      longitude: fix.longitude,
      speedMps: fix.speedMps,
    });
    try {
      const row = await o.record(decision, simulated);   // primero SQLite
      patch({
        lastFix: {
          eventId: row.event_id, capturedAt: row.captured_at, lat: row.lat, lon: row.lon,
          speedKmh: row.speed_kmh, skippedReason: decision.kind === "skipped" ? decision.reason : null,
          simulated,
        },
      });
      await refresh();
      if (decision.kind === "event") void runSync("capture");
    } catch (err) {
      patch({ notice: `No se pudo guardar la posición: ${String(err)}` });
    }
  }, [patch, refresh, runSync]);

  const stopSources = useCallback(() => {
    watcher.current?.remove();
    watcher.current = null;
    if (simTimer.current) clearInterval(simTimer.current);
    simTimer.current = null;
  }, []);

  const startSources = useCallback(async () => {
    stopSources();
    if (simulatedRef.current) {
      simulator.current = new RouteSimulator(unknownSpeedRef.current ? 6 : 0);
      const tick = () => {
        const f = simulator.current!.next(Date.now(), APP_CONFIG.captureIntervalMs);
        void handleFix(f, true);
      };
      tick();
      simTimer.current = setInterval(tick, APP_CONFIG.captureIntervalMs);
      return true;
    }
    const perm = await Location.getForegroundPermissionsAsync();
    if (!perm.granted) {
      patch({ permission: perm.canAskAgain ? "denied" : "blocked",
              notice: "Sin permiso de ubicación: concédelo o usa el modo simulado" });
      return false;
    }
    watcher.current = await Location.watchPositionAsync(
      { accuracy: Location.Accuracy.High, timeInterval: APP_CONFIG.captureIntervalMs, distanceInterval: 0 },
      (loc) => void handleFix({ latitude: loc.coords.latitude, longitude: loc.coords.longitude,
                                speedMps: loc.coords.speed, timestampMs: loc.timestamp }, false),
    );
    return true;
  }, [handleFix, patch, stopSources]);

  const startCapture = useCallback(async () => {
    const v = normalizeVehicleId(settingsRef.current.vehicleId);
    if (!v.ok) {
      patch({ notice: v.error });
      return;
    }
    wantCapture.current = true;
    const started = await startSources();
    if (!started) wantCapture.current = false;
    patch({ capture: started ? "capturing" : "stopped" });
  }, [patch, startSources]);

  const stopCapture = useCallback(() => {
    wantCapture.current = false;
    stopSources();
    patch({ capture: "stopped" });
  }, [patch, stopSources]);

  // -- segundo plano: la captura se pausa; al volver se reanuda ----------------------
  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      const prev = appState.current;
      appState.current = next;
      if (next !== "active" && prev === "active") {
        stopSources();
        if (wantCapture.current) patch({ capture: "paused" });
      } else if (next === "active" && prev !== "active") {
        if (wantCapture.current) {
          void startSources().then((ok) => patch({ capture: ok ? "capturing" : "stopped" }));
        }
        void runSync("open");
      }
    });
    return () => sub.remove();
  }, [patch, runSync, startSources, stopSources]);

  useEffect(() => () => stopSources(), [stopSources]);

  // -- acciones ------------------------------------------------------------------------
  const requestPermission = useCallback(async () => {
    const r = await Location.requestForegroundPermissionsAsync();
    patch({ permission: r.granted ? "granted" : r.canAskAgain ? "denied" : "blocked" });
  }, [patch]);

  const saveSettings = useCallback(async (apiUrl: string, vehicleId: string) => {
    const url = normalizeApiUrl(apiUrl);
    if (!url.ok) return url.error;
    const vehicle = normalizeVehicleId(vehicleId);
    if (!vehicle.ok) return vehicle.error;
    const next = { apiUrl: url.value, vehicleId: vehicle.value };
    await store.current?.save(next);
    settingsRef.current = next;     // los eventos ya guardados conservan su vehículo
    patch({ settings: next, notice: "Configuración guardada" });
    return null;
  }, [patch]);

  const setSimulated = useCallback(async (value: boolean) => {
    simulatedRef.current = value;
    patch({ simulated: value });
    if (wantCapture.current) {
      const ok = await startSources();
      patch({ capture: ok ? "capturing" : "stopped" });
    }
  }, [patch, startSources]);

  const setSimulateUnknownSpeed = useCallback(async (value: boolean) => {
    unknownSpeedRef.current = value;
    patch({ simulateUnknownSpeed: value });
    if (wantCapture.current && simulatedRef.current) await startSources();
  }, [patch, startSources]);

  return {
    state,
    actions: {
      requestPermission, saveSettings, startCapture, stopCapture, setSimulated, setSimulateUnknownSpeed,
      syncNow: () => runSync("manual"),
      dismissNotice: () => patch({ notice: null }),
    },
  };
}
