/**
 * MODO SIMULADO: genera lecturas como las del GPS (m/s y epoch ms) sobre un
 * circuito de Bogotá, para probar en un emulador o sin desplazarse. Pasan por
 * la misma conversión y validación que las lecturas reales y se marcan como
 * simuladas en la base local y en la interfaz.
 */

export interface SimulatedFix {
  latitude: number;
  longitude: number;
  speedMps: number | null;
  timestampMs: number;
}

const CENTER = { lat: 4.6486, lon: -74.0628 }; // Chapinero (ficticio)
const RADIUS_M = 600;
const M_PER_DEG_LAT = 111_195;

export class RouteSimulator {
  private angle = 0;
  private tick = 0;

  /** `unknownSpeedEvery`: cada cuántas lecturas omitir la velocidad (0 = nunca). */
  constructor(private readonly unknownSpeedEvery = 0) {}

  next(nowMs: number, intervalMs: number): SimulatedFix {
    this.tick++;
    // Paradas cortas cada 12 lecturas para ver velocidades de 0 km/h reales.
    const stopped = this.tick % 12 >= 9;
    const speedMps = stopped ? 0 : 8 + 4 * Math.sin(this.tick / 5); // ~14–43 km/h
    this.angle += (speedMps * (intervalMs / 1000)) / RADIUS_M;
    const dLat = (RADIUS_M * Math.sin(this.angle)) / M_PER_DEG_LAT;
    const dLon = (RADIUS_M * Math.cos(this.angle)) / (M_PER_DEG_LAT * Math.cos((CENTER.lat * Math.PI) / 180));
    const unknown = this.unknownSpeedEvery > 0 && this.tick % this.unknownSpeedEvery === 0;
    return {
      latitude: Number((CENTER.lat + dLat).toFixed(6)),
      longitude: Number((CENTER.lon + dLon).toFixed(6)),
      speedMps: unknown ? null : speedMps,
      timestampMs: nowMs,
    };
  }
}
