import type { Vehicle } from "../api/types";
import { ageSeconds, formatAge } from "../lib/freshness";

interface Props {
  vehicles: Vehicle[];
  totalCount: number;
  query: string;
  onQueryChange: (q: string) => void;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
  now: number;
  isVehicleStale: (v: Vehicle) => boolean;
}

export function VehicleList({
  vehicles, totalCount, query, onQueryChange, selectedId, onSelect, now, isVehicleStale,
}: Props) {
  return (
    <section className="list-panel" aria-label="Vehículos">
      <label className="search">
        <span className="sr-only">Buscar vehículo por identificador</span>
        <input
          type="search"
          placeholder="Buscar por identificador…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          autoComplete="off"
        />
      </label>
      <p className="muted small list-count">
        {query ? `${vehicles.length} de ${totalCount}` : `${totalCount} en total`}
      </p>
      {vehicles.length === 0 && query && (
        <p className="muted list-empty">Ningún vehículo coincide con “{query}”.</p>
      )}
      <ul className="vehicle-list">
        {vehicles.map((v) => {
          const stale = isVehicleStale(v);
          const selected = v.vehicle_id === selectedId;
          return (
            <li key={v.vehicle_id}>
              <button
                type="button"
                className={`vehicle-item${selected ? " selected" : ""}${stale ? " stale" : ""}`}
                aria-pressed={selected}
                onClick={() => onSelect(v.vehicle_id)}
              >
                <span className="vehicle-id">{v.vehicle_id}</span>
                <span className="vehicle-speed">{Math.round(v.speed_kmh)} km/h</span>
                <span className="vehicle-age">
                  {stale && <span className="badge-stale">Desactualizado</span>}
                  {formatAge(ageSeconds(v.captured_at, now))}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
