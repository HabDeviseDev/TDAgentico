import { useCallback, useMemo, useState, type ReactNode } from "react";
import type { Vehicle } from "./api/types";
import type { PortalConfig } from "./config";
import { AlertsPanel } from "./components/AlertsPanel";
import { ChatPanel } from "./components/ChatPanel";
import { MapView } from "./components/MapView";
import { StatusBar } from "./components/StatusBar";
import { VehicleDetail } from "./components/VehicleDetail";
import { VehicleList } from "./components/VehicleList";
import { useNow } from "./hooks/useNow";
import { useVehicleStream } from "./hooks/useVehicleStream";
import { useZones } from "./hooks/useZones";
import { browserTimeZone, formatTimeLocal, isStale } from "./lib/freshness";

const STREAM_URL = "/v1/vehicles/stream";

export function App({ config }: { config: PortalConfig }) {
  const [state, dispatch] = useVehicleStream(STREAM_URL, config.connectionTimeoutSeconds);
  const [query, setQuery] = useState("");
  const now = useNow(5_000);
  const zones = useZones();
  const [chatOpen, setChatOpen] = useState(false);

  const isVehicleStale = useCallback(
    (v: Vehicle) => isStale(v.captured_at, now, config.staleAfterSeconds),
    [now, config.staleAfterSeconds],
  );
  const select = useCallback(
    (vehicleId: string | null) => dispatch({ type: "select", vehicleId }),
    [dispatch],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? state.vehicles.filter((v) => v.vehicle_id.toLowerCase().includes(q)) : state.vehicles;
  }, [query, state.vehicles]);

  const selected = state.vehicles.find((v) => v.vehicle_id === state.selectedId);
  const staleCount = state.vehicles.filter(isVehicleStale).length;

  const alertVehicleIds = useMemo(
    () => new Set((state.alerts?.alerts ?? []).map((a) => a.vehicle_id)),
    [state.alerts],
  );
  const selectedAlerts = (state.alerts?.alerts ?? []).filter((a) => a.vehicle_id === state.selectedId);
  const selectedInsufficient = state.alerts?.insufficient_evidence.find(
    (i) => i.vehicle_id === state.selectedId,
  );
  const alertsOutdated =
    state.alerts !== null && (state.alertsState === "degraded" || state.connection === "lost");
  const hasDemoZones = zones.zones.some((z) => z.is_demo);

  // Desde el chat: seleccionar y, en pantallas angostas, cerrar el panel para ver el mapa.
  const selectFromChat = useCallback(
    (vehicleId: string) => {
      select(vehicleId);
      if (window.matchMedia("(max-width: 900px)").matches) setChatOpen(false);
    },
    [select],
  );

  const content = (() => {
    if (!state.hasSnapshot) {
      if (state.data === "degraded") {
        return (
          <StateMessage kind="error" title="No se pudieron cargar los vehículos">
            La base de datos no responde. El portal se actualizará solo cuando se recupere.
          </StateMessage>
        );
      }
      if (state.connection === "lost") {
        return (
          <StateMessage kind="error" title="Sin conexión con el servidor">
            Reintentando la conexión en tiempo real…
          </StateMessage>
        );
      }
      return <StateMessage kind="loading" title="Cargando vehículos…">Conectando en tiempo real.</StateMessage>;
    }
    if (state.vehicles.length === 0) {
      return (
        <StateMessage kind="empty" title="Aún no hay vehículos">
          Ningún vehículo tiene posiciones procesadas. Aparecerán aquí en cuanto lleguen.
        </StateMessage>
      );
    }
    return null;
  })();

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Flota en tiempo real</h1>
          <p className="muted small">Horas en {browserTimeZone()}</p>
        </div>
        <StatusBar
          total={state.vehicles.length}
          staleCount={staleCount}
          connection={state.connection}
          data={state.data}
          snapshotAt={state.snapshotAt}
          alertCount={state.alerts?.alert_count ?? null}
          alertsOutdated={alertsOutdated}
        />
        <button
          type="button"
          className="primary-button chat-toggle"
          aria-expanded={chatOpen}
          onClick={() => setChatOpen((o) => !o)}
        >
          {chatOpen ? "Cerrar asistente" : "Asistente"}
        </button>
      </header>

      {state.hasSnapshot && state.connection === "lost" && (
        <div className="banner banner-error" role="alert">
          Se perdió la conexión en tiempo real. Se muestran los últimos datos recibidos
          {state.snapshotAt ? ` (snapshot de las ${formatTimeLocal(state.snapshotAt)})` : ""}.
          Reintentando…
        </div>
      )}
      {state.hasSnapshot && state.data === "degraded" && (
        <div className="banner banner-warn" role="alert">
          La base de datos no responde: los datos visibles pueden no estar actualizados.
          {state.dataDetail ? ` ${state.dataDetail}.` : ""}
        </div>
      )}

      <main className="layout">
        <div className="map-panel">
          <MapView
            vehicles={state.vehicles}
            selectedId={state.selectedId}
            isVehicleStale={isVehicleStale}
            onSelect={select}
            zones={zones.zones}
            alertVehicleIds={alertVehicleIds}
          />
          {content && <div className="map-overlay">{content}</div>}
          <div className="map-legend" aria-label="Leyenda del mapa">
            {hasDemoZones && (
              <span>
                <span className="legend-zone" aria-hidden="true" /> Zonas de demostración (ficticias)
              </span>
            )}
            <span>
              <span className="legend-dot legend-alert" aria-hidden="true" /> Con alerta de detención
            </span>
            {zones.error && <span className="legend-error">Zonas no disponibles: reintentando…</span>}
          </div>
        </div>
        <aside className="sidebar">
          <AlertsPanel
            alerts={state.alerts}
            alertsState={state.alertsState}
            alertsDetail={state.alertsDetail}
            lastEvaluationAt={state.alertsLastEvaluationAt}
            connection={state.connection}
            selectedId={state.selectedId}
            onSelect={select}
            now={now}
          />
          <VehicleDetail
            vehicle={selected}
            now={now}
            stale={selected ? isVehicleStale(selected) : false}
            staleAfterSeconds={config.staleAfterSeconds}
            onClose={() => select(null)}
            alerts={selectedAlerts}
            insufficient={selectedInsufficient}
          />
          <VehicleList
            vehicles={filtered}
            totalCount={state.vehicles.length}
            query={query}
            onQueryChange={setQuery}
            selectedId={state.selectedId}
            onSelect={select}
            now={now}
            isVehicleStale={isVehicleStale}
          />
        </aside>
      </main>
      <ChatPanel
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        selectedId={state.selectedId}
        onSelectVehicle={selectFromChat}
      />
    </div>
  );
}

function StateMessage({ kind, title, children }: {
  kind: "loading" | "error" | "empty"; title: string; children: ReactNode;
}) {
  return (
    <div className={`state-message state-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {kind === "loading" && <span className="spinner" aria-hidden="true" />}
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}
