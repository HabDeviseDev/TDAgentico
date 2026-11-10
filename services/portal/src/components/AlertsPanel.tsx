import type { AlertsEvent } from "../api/types";
import type { ConnectionState, DataState } from "../state/fleetState";
import { formatDuration, formatObservedDuration, reasonLabel } from "../lib/duration";
import { ageSeconds, formatAge, formatTimeLocal } from "../lib/freshness";

interface Props {
  alerts: AlertsEvent | null;
  alertsState: DataState;
  alertsDetail: string | null;
  lastEvaluationAt: string | null;
  connection: ConnectionState;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
  now: number;
}

export function AlertsPanel({
  alerts, alertsState, alertsDetail, lastEvaluationAt, connection, selectedId, onSelect, now,
}: Props) {
  const outdated = alertsState === "degraded" || (connection === "lost" && alerts !== null);
  const count = alerts?.alert_count ?? null;

  return (
    <section className="alerts-panel" aria-label="Alertas de detención">
      <header className="alerts-header">
        <h2>
          Alertas activas{" "}
          <span className={`count-badge${count ? " count-badge-alert" : ""}`}>
            {count === null ? "–" : count}
          </span>
        </h2>
        {alerts && (
          <span
            className="muted small"
            title={`Evaluación que produjo este resultado: ${alerts.evaluated_at} (UTC). Las evaluaciones posteriores sin cambios no se retransmiten.`}
          >
            Sin cambios desde {formatTimeLocal(alerts.evaluated_at)}
          </span>
        )}
      </header>

      {alerts === null && alertsState === "degraded" && (
        <p className="notice notice-error" role="alert">
          No se pudieron evaluar las alertas{alertsDetail ? `: ${alertsDetail}` : ""}. No hay un
          resultado que mostrar; se reintenta automáticamente.
        </p>
      )}
      {alerts === null && alertsState !== "degraded" && (
        <p className="muted small">
          {connection === "lost" ? "Sin conexión: aún no se recibió ninguna evaluación." : "Evaluando alertas…"}
        </p>
      )}

      {alerts && outdated && (
        <p className="notice notice-warn" role="alert">
          {alertsState === "degraded"
            ? `Resultado desactualizado: la evaluación falló${alertsDetail ? ` (${alertsDetail})` : ""}.`
            : "Sin conexión: se muestra el último resultado recibido."}
          {lastEvaluationAt ? ` Última evaluación correcta: ${formatTimeLocal(lastEvaluationAt)}` : ""}
        </p>
      )}
      {alerts && !alerts.complete && (
        <p className="notice notice-warn" role="alert">
          Evaluación parcial: {alerts.coverage.vehicles_evaluated} de{" "}
          {alerts.coverage.vehicles_in_window} vehículos evaluados (
          {alerts.incomplete_reasons.map(reasonLabel).join("; ")}). Las alertas listadas no cubren
          toda la flota.
        </p>
      )}

      {alerts && alerts.alerts.length === 0 && (
        <p className="muted small">
          {alerts.complete
            ? "Sin alertas activas."
            : "Sin alertas entre los vehículos evaluados."}
        </p>
      )}

      {alerts && alerts.alerts.length > 0 && (
        <ul className="alert-list">
          {alerts.alerts.map((a) => (
            <li key={a.alert_id}>
              <button
                type="button"
                className={`alert-item${a.vehicle_id === selectedId ? " selected" : ""}`}
                aria-pressed={a.vehicle_id === selectedId}
                onClick={() => onSelect(a.vehicle_id)}
              >
                <span className="alert-vehicle">{a.vehicle_id}</span>
                <span className="alert-duration" title="Duración observada entre la primera y la última muestra">
                  {formatObservedDuration(a.observed_duration_s, a.start_unknown)}
                </span>
                <span className="alert-zone">{a.zone_name}</span>
                <span className="alert-meta">
                  Última muestra {formatTimeLocal(a.last_sample_at)} ·{" "}
                  {formatAge(ageSeconds(a.last_sample_at, now))}
                  {a.start_unknown && (
                    <span className="badge-unknown" title="El tramo empieza en el límite de la ventana histórica">
                      Inicio desconocido
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {alerts && alerts.insufficient_evidence.length > 0 && (
        <details className="insufficient">
          <summary>
            Evidencia insuficiente ({alerts.insufficient_evidence_count})
          </summary>
          <ul>
            {alerts.insufficient_evidence.map((i) => (
              <li key={i.vehicle_id}>
                <button type="button" className="link-button" onClick={() => onSelect(i.vehicle_id)}>
                  {i.vehicle_id}
                </button>{" "}
                <span className="muted small">
                  {reasonLabel(i.reason)}
                  {i.last_sample_at ? ` · ${formatAge(ageSeconds(i.last_sample_at, now))}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {alerts && (
        <p className="muted small rule-summary">
          Regla: ≤ {alerts.parameters.max_speed_kmh} km/h, ≤ {alerts.parameters.max_displacement_m} m de la
          primera muestra, muestras cada ≤ {alerts.parameters.max_gap_s} s, alerta con más de{" "}
          {formatDuration(alerts.parameters.alert_after_s)}. Ventana{" "}
          {formatDuration(alerts.parameters.window_s)}.
        </p>
      )}
    </section>
  );
}
