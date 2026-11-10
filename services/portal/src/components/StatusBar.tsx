import type { ConnectionState, DataState } from "../state/fleetState";
import { formatTimeLocal } from "../lib/freshness";

interface Props {
  total: number;
  staleCount: number;
  connection: ConnectionState;
  data: DataState;
  snapshotAt: string | null;
  /** null: todavía no hay resultado de alertas. */
  alertCount: number | null;
  alertsOutdated: boolean;
}

const connectionLabel: Record<ConnectionState, string> = {
  connecting: "Conectando…",
  open: "En tiempo real",
  lost: "Conexión perdida · reintentando",
};

const dataLabel: Record<DataState, string> = {
  unknown: "Datos: esperando",
  ok: "Datos: al día",
  degraded: "Datos: BD sin respuesta",
};

export function StatusBar({
  total, staleCount, connection, data, snapshotAt, alertCount, alertsOutdated,
}: Props) {
  return (
    <div className="status-bar" role="status" aria-live="polite">
      <span className="metric">
        <strong>{total}</strong> {total === 1 ? "vehículo" : "vehículos"}
      </span>
      {staleCount > 0 && (
        <span className="metric metric-warn">
          <strong>{staleCount}</strong> desactualizado{staleCount === 1 ? "" : "s"}
        </span>
      )}
      <span
        className={`metric${alertCount ? " metric-alert" : ""}`}
        title={alertsOutdated ? "Último resultado conocido: puede no estar actualizado" : "Alertas de detención activas"}
      >
        <strong>{alertCount === null ? "–" : alertCount}</strong>{" "}
        {alertCount === 1 ? "alerta" : "alertas"}
        {alertsOutdated ? " (desactualizado)" : ""}
      </span>
      <span className={`pill pill-${connection}`} title="Conexión con el servidor">
        <span className="dot" aria-hidden="true" />
        {connectionLabel[connection]}
      </span>
      {connection === "lost" && data !== "unknown" ? (
        // Sin conexión no sabemos si la BD sigue al día: el estado de los
        // datos es el último conocido, no uno confirmado.
        <span className="pill pill-data-unknown" title="Lectura de la base de datos">
          Datos: último snapshot recibido
        </span>
      ) : (
        <span className={`pill pill-data-${data}`} title="Lectura de la base de datos">
          {dataLabel[data]}
        </span>
      )}
      {snapshotAt && (
        <span className="muted small" title={`${snapshotAt} (UTC)`}>
          Snapshot {formatTimeLocal(snapshotAt)}
        </span>
      )}
    </div>
  );
}
