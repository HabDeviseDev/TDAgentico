/** Duración legible: "21 min 30 s", "2 h 05 min". */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h} h ${String(m).padStart(2, "0")} min`;
  if (m > 0) return sec ? `${m} min ${sec} s` : `${m} min`;
  return `${sec} s`;
}

/** Duración observada de una alerta; con inicio desconocido es un mínimo. */
export function formatObservedDuration(seconds: number, startUnknown: boolean): string {
  return startUnknown ? `≥ ${formatDuration(seconds)}` : formatDuration(seconds);
}

const reasonLabels: Record<string, string> = {
  stale_last_sample: "Última muestra desactualizada",
  max_vehicles: "se alcanzó el máximo de vehículos por evaluación",
  max_rows: "se alcanzó el máximo de filas de historial por evaluación",
};

export function reasonLabel(code: string): string {
  return reasonLabels[code] ?? code;
}
