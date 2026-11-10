/** Antigüedad del dato según captured_at (no según la hora de llegada). */
export function ageSeconds(capturedAtIso: string, nowMs: number): number {
  return Math.max(0, (nowMs - Date.parse(capturedAtIso)) / 1000);
}

export function isStale(capturedAtIso: string, nowMs: number, staleAfterSeconds: number): boolean {
  return ageSeconds(capturedAtIso, nowMs) > staleAfterSeconds;
}

const relative = new Intl.RelativeTimeFormat("es", { numeric: "auto" });

export function formatAge(seconds: number): string {
  if (seconds < 60) return relative.format(-Math.round(seconds), "second");
  if (seconds < 3600) return relative.format(-Math.floor(seconds / 60), "minute");
  if (seconds < 86400) return relative.format(-Math.floor(seconds / 3600), "hour");
  return relative.format(-Math.floor(seconds / 86400), "day");
}

/** Fecha en la zona horaria del navegador. */
export function formatLocal(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(iso));
}

export function formatTimeLocal(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(new Date(iso));
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}
