import { useEffect, useRef } from "react";
import { Circle, CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import type { Vehicle, Zone } from "../api/types";

const BOGOTA: [number, number] = [4.65, -74.08];

interface Props {
  vehicles: Vehicle[];
  selectedId: string | null;
  isVehicleStale: (v: Vehicle) => boolean;
  onSelect: (vehicleId: string) => void;
  zones: Zone[];
  /** Vehículos con al menos una alerta activa. */
  alertVehicleIds: Set<string>;
}

/** Encuadra todos los vehículos la primera vez que llegan datos. */
function FitOnFirstData({ vehicles }: { vehicles: Vehicle[] }) {
  const map = useMap();
  const done = useRef(false);
  useEffect(() => {
    if (done.current || vehicles.length === 0) return;
    done.current = true;
    if (vehicles.length === 1) {
      map.setView([vehicles[0].lat, vehicles[0].lon], 14);
      return;
    }
    const bounds: LatLngBoundsExpression = vehicles.map((v) => [v.lat, v.lon]);
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
  }, [map, vehicles]);
  return null;
}

/** Centra (y acerca si hace falta) el mapa al cambiar la selección. */
function FollowSelection({ vehicle }: { vehicle: Vehicle | undefined }) {
  const map = useMap();
  const id = vehicle?.vehicle_id;
  useEffect(() => {
    if (!vehicle) return;
    map.flyTo([vehicle.lat, vehicle.lon], Math.max(map.getZoom(), 14), { duration: 0.6 });
    // Solo al cambiar de vehículo; no persigue cada actualización.
  }, [map, id]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export function MapView({
  vehicles, selectedId, isVehicleStale, onSelect, zones, alertVehicleIds,
}: Props) {
  const selected = vehicles.find((v) => v.vehicle_id === selectedId);
  return (
    <MapContainer center={BOGOTA} zoom={12} className="map" scrollWheelZoom>
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        maxZoom={19}
      />
      {zones.map((z) => (
        <Circle
          key={z.zone_id}
          center={[z.center_lat, z.center_lon]}
          radius={z.radius_m}
          interactive={false}
          pathOptions={{
            color: "#c5221f",
            weight: 2,
            dashArray: z.is_demo ? "6 6" : undefined,
            fillColor: "#c5221f",
            fillOpacity: 0.08,
          }}
        >
          <Tooltip permanent direction="center" className="zone-label">
            {z.name}
            {z.is_demo && !z.name.toLowerCase().startsWith("demo") ? " (demo)" : ""}
          </Tooltip>
        </Circle>
      ))}
      {vehicles.map((v) => {
        const isSelected = v.vehicle_id === selectedId;
        const stale = isVehicleStale(v);
        const alerting = alertVehicleIds.has(v.vehicle_id);
        return (
          <CircleMarker
            key={v.vehicle_id}
            center={[v.lat, v.lon]}
            radius={isSelected ? 11 : 7}
            pathOptions={{
              color: isSelected ? "#0b3d91" : "#ffffff",
              weight: isSelected ? 3 : 2,
              fillColor: alerting ? "#c5221f" : stale ? "#9aa3ad" : "#1a73e8",
              fillOpacity: 0.95,
            }}
            eventHandlers={{ click: () => onSelect(v.vehicle_id) }}
          >
            <Tooltip direction="top" offset={[0, -8]}>
              {v.vehicle_id} · {Math.round(v.speed_kmh)} km/h{stale ? " · desactualizado" : ""}
              {alerting ? " · alerta de detención" : ""}
            </Tooltip>
          </CircleMarker>
        );
      })}
      <FitOnFirstData vehicles={vehicles} />
      <FollowSelection vehicle={selected} />
    </MapContainer>
  );
}
