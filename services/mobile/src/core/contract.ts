/**
 * Contrato del evento de posición, con la misma semántica que
 * `contracts/fleet_contracts` (PositionEvent / VehicleId) en el backend:
 * - vehicle_id: de 1 a 64 caracteres (se mide el texto recibido) y no vacío
 *   después de quitar los espacios de los extremos. Distingue mayúsculas.
 * - lat [-90, 90], lon [-180, 180], speed_kmh [0, 300].
 * - captured_at: ISO-8601 con zona horaria (aquí siempre UTC con "Z").
 * La regla "no más de 5 min en el futuro" la aplica la API; si el reloj del
 * teléfono está adelantado, el evento vuelve como `rejected` con el motivo.
 */

export const SCHEMA_VERSION = 1;
export const VEHICLE_ID_MAX_LENGTH = 64;
export const SPEED_MAX_KMH = 300;
export const MPS_TO_KMH = 3.6;

export interface PositionEvent {
  schema_version: 1;
  event_id: string;
  vehicle_id: string;
  captured_at: string;
  lat: number;
  lon: number;
  speed_kmh: number;
}

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

/** Igual que VehicleId: primero la longitud del texto recibido, luego el recorte. */
export function normalizeVehicleId(raw: string): Checked<string> {
  if (raw.length < 1 || raw.length > VEHICLE_ID_MAX_LENGTH) {
    return { ok: false, error: `El vehículo debe tener entre 1 y ${VEHICLE_ID_MAX_LENGTH} caracteres` };
  }
  const value = raw.trim();
  if (!value) return { ok: false, error: "El vehículo no puede estar vacío" };
  return { ok: true, value };
}

export type SpeedReading =
  | { kind: "known"; kmh: number }
  | { kind: "unknown"; reason: string };

/**
 * Velocidad del dispositivo (m/s, como la entrega expo-location) a km/h.
 * Una velocidad ausente, negativa (Android/iOS usan -1 para "no disponible"),
 * no numérica o fuera del contrato NO se convierte en 0: queda desconocida.
 */
export function readSpeed(metersPerSecond: number | null | undefined): SpeedReading {
  if (metersPerSecond === null || metersPerSecond === undefined) {
    return { kind: "unknown", reason: "El dispositivo no informó la velocidad" };
  }
  if (!Number.isFinite(metersPerSecond)) {
    return { kind: "unknown", reason: "Velocidad no numérica" };
  }
  if (metersPerSecond < 0) {
    return { kind: "unknown", reason: "Velocidad no disponible en el dispositivo (valor negativo)" };
  }
  const kmh = Math.round(metersPerSecond * MPS_TO_KMH * 100) / 100;
  if (kmh > SPEED_MAX_KMH) {
    return { kind: "unknown", reason: `Velocidad fuera de rango (${kmh} km/h > ${SPEED_MAX_KMH})` };
  }
  return { kind: "known", kmh };
}

/** Fecha de captura (epoch en ms) a ISO-8601 UTC con milisegundos y "Z". */
export function toUtcIso(epochMs: number): string {
  return new Date(epochMs).toISOString();
}

export interface RawFix {
  eventId: string;
  vehicleId: string;
  /** Hora de la medición según el dispositivo (epoch ms). */
  timestampMs: number;
  latitude: number;
  longitude: number;
  /** m/s; null si el dispositivo no la informa. */
  speedMps: number | null | undefined;
}

export type SampleDecision =
  | { kind: "event"; event: PositionEvent }
  | {
      kind: "skipped";
      reason: string;
      eventId: string;
      vehicleId: string;
      capturedAt: string | null;
      lat: number | null;
      lon: number | null;
    };

/** Decide si una lectura del GPS es un evento válido o se omite (con motivo). */
export function buildSample(fix: RawFix): SampleDecision {
  const finite = (n: number) => (Number.isFinite(n) ? n : null);
  const capturedAt = Number.isFinite(fix.timestampMs) ? toUtcIso(fix.timestampMs) : null;
  const skip = (reason: string): SampleDecision => ({
    kind: "skipped",
    reason,
    eventId: fix.eventId,
    vehicleId: fix.vehicleId,
    capturedAt,
    lat: finite(fix.latitude),
    lon: finite(fix.longitude),
  });

  const vehicle = normalizeVehicleId(fix.vehicleId);
  if (!vehicle.ok) return skip(vehicle.error);
  if (capturedAt === null) return skip("Fecha de captura inválida");
  if (!Number.isFinite(fix.latitude) || fix.latitude < -90 || fix.latitude > 90) {
    return skip("Latitud fuera de rango");
  }
  if (!Number.isFinite(fix.longitude) || fix.longitude < -180 || fix.longitude > 180) {
    return skip("Longitud fuera de rango");
  }
  const speed = readSpeed(fix.speedMps);
  if (speed.kind === "unknown") return skip(speed.reason);

  return {
    kind: "event",
    event: {
      schema_version: SCHEMA_VERSION,
      event_id: fix.eventId,
      vehicle_id: vehicle.value,
      captured_at: capturedAt,
      lat: fix.latitude,
      lon: fix.longitude,
      speed_kmh: speed.kmh,
    },
  };
}
