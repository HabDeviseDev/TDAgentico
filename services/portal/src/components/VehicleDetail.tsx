import type { InsufficientEvidence, StopAlert, Vehicle } from "../api/types";
import { formatObservedDuration } from "../lib/duration";
import { ageSeconds, formatAge, formatLocal } from "../lib/freshness";

interface Props {
  vehicle: Vehicle | undefined;
  now: number;
  stale: boolean;
  staleAfterSeconds: number;
  onClose: () => void;
  alerts: StopAlert[];
  insufficient: InsufficientEvidence | undefined;
}

export function VehicleDetail({
  vehicle, now, stale, staleAfterSeconds, onClose, alerts, insufficient,
}: Props) {
  if (!vehicle) {
    return (
      <section className="detail-panel detail-empty" aria-label="Detalle">
        <p className="muted">Selecciona un vehículo en el mapa o en la lista para ver su detalle.</p>
      </section>
    );
  }
  const age = ageSeconds(vehicle.captured_at, now);
  return (
    <section className="detail-panel" aria-label={`Detalle de ${vehicle.vehicle_id}`}>
      <header className="detail-header">
        <h2>{vehicle.vehicle_id}</h2>
        <button type="button" className="link-button" onClick={onClose}>
          Cerrar
        </button>
      </header>
      {stale && (
        <p className="notice notice-warn">
          Posición desactualizada: la última lectura tiene más de {formatThreshold(staleAfterSeconds)}.
        </p>
      )}
      {alerts.map((a) => (
        <p key={a.alert_id} className="notice notice-error">
          <strong>Detenido en {a.zone_name}</strong>
          <span className="hint-block">
            Duración observada: {formatObservedDuration(a.observed_duration_s, a.start_unknown)}
          </span>
          <span className="hint-block">
            Desde {formatLocal(a.started_at)}
            {a.start_unknown ? " (inicio exacto desconocido: el tramo empieza en el límite de la ventana)" : ""}
          </span>
          <span className="hint-block">Última muestra {formatLocal(a.last_sample_at)}</span>
        </p>
      ))}
      {insufficient && (
        <p className="notice notice-warn">Alertas: evidencia insuficiente. {insufficient.detail}</p>
      )}
      <dl className="detail-grid">
        <dt>Velocidad</dt>
        <dd>
          <span className="big">{vehicle.speed_kmh.toFixed(1)}</span> km/h
          <span className="hint">
            Velocidad de la última lectura recibida.
          </span>
        </dd>
        <dt>Coordenadas</dt>
        <dd className="mono">
          {vehicle.lat.toFixed(6)}, {vehicle.lon.toFixed(6)}
        </dd>
        <dt>Capturada</dt>
        <dd title={`${vehicle.captured_at} (UTC)`}>
          {formatLocal(vehicle.captured_at)}
          <span className="hint">{formatAge(age)}</span>
        </dd>
        <dt>Procesada</dt>
        <dd title={`${vehicle.updated_at} (UTC)`}>{formatLocal(vehicle.updated_at)}</dd>
        <dt>Evento</dt>
        <dd className="mono small">{vehicle.event_id}</dd>
      </dl>
    </section>
  );
}

function formatThreshold(seconds: number): string {
  if (seconds % 60 === 0) {
    const m = seconds / 60;
    return `${m} ${m === 1 ? "minuto" : "minutos"}`;
  }
  return `${seconds} segundos`;
}
