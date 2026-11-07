/**
 * Ciclo de vida de la captura (GPS o simulación), sin React Native.
 *
 * Cada inicio recibe una GENERACIÓN. Detener, pasar a segundo plano, cambiar
 * de fuente, un nuevo inicio o desmontar incrementan la generación y anulan
 * los inicios anteriores:
 * - un inicio anulado no cambia el estado visible;
 * - si su suscripción termina de crearse tarde, se elimina en el acto;
 * - las lecturas de una suscripción anulada se descartan (aunque la fuente
 *   todavía entregue alguna después de `remove()`).
 * Solo el inicio vigente puede mostrar "capturando" y aceptar lecturas.
 * Las posiciones ya guardadas en SQLite no se tocan.
 */

export type CaptureMode = "gps" | "simulated";
export type CaptureStatus = "stopped" | "starting" | "capturing" | "paused";
export type PermissionResult = "granted" | "denied" | "blocked";

export interface Fix {
  latitude: number;
  longitude: number;
  speedMps: number | null;
  timestampMs: number;
}

export interface Subscription {
  remove(): void;
}

export interface FixSource {
  /** Pide o comprueba el permiso (la simulación no lo necesita). */
  ensurePermission(): Promise<PermissionResult>;
  /** Crea la suscripción; puede tardar (el GPS se inicializa). */
  watch(onFix: (fix: Fix) => void): Promise<Subscription>;
}

export interface CaptureEvents {
  onStatus(status: CaptureStatus, mode: CaptureMode): void;
  onPermission(result: PermissionResult): void;
  /** Lectura aceptada del inicio vigente. Puede ser asíncrona (guardar en SQLite). */
  onFix(fix: Fix, mode: CaptureMode): void | Promise<void>;
  onError(message: string): void;
}

export class CaptureController {
  private generation = 0;
  private subscription: Subscription | null = null;
  private wanted = false;          // el usuario pidió capturar
  private foreground = true;
  private disposed = false;
  private mode: CaptureMode = "gps";
  private status: CaptureStatus = "stopped";

  constructor(
    private readonly sources: Record<CaptureMode, FixSource>,
    private readonly events: CaptureEvents,
  ) {}

  get currentStatus(): CaptureStatus {
    return this.status;
  }

  get currentMode(): CaptureMode {
    return this.mode;
  }

  /** Para pruebas y diagnóstico: hay una suscripción vigente. */
  get hasSubscription(): boolean {
    return this.subscription !== null;
  }

  start(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    this.wanted = true;
    return this.activate();
  }

  stop(): void {
    this.wanted = false;
    this.invalidate();
    this.setStatus("stopped");
  }

  /** Cambia entre GPS y simulación; si estaba capturando, reinicia con la nueva fuente. */
  setMode(mode: CaptureMode): Promise<void> {
    if (this.disposed || mode === this.mode) return Promise.resolve();
    this.mode = mode;
    if (!this.wanted) return Promise.resolve();
    return this.activate();
  }

  /** Reinicia la fuente actual (p. ej. cambió una opción del simulador). */
  restart(): Promise<void> {
    if (this.disposed || !this.wanted) return Promise.resolve();
    return this.activate();
  }

  /** La app pasó a segundo plano: se suelta la fuente y se conserva la intención. */
  background(): void {
    this.foreground = false;
    this.invalidate();
    if (this.wanted) this.setStatus("paused");
  }

  /** La app volvió a primer plano: si el usuario quería capturar, se reanuda. */
  foregrounded(): Promise<void> {
    this.foreground = true;
    if (this.disposed || !this.wanted) return Promise.resolve();
    return this.activate();
  }

  /** Desmontaje: nada más cambia el estado ni acepta lecturas. */
  dispose(): void {
    this.disposed = true;
    this.wanted = false;
    this.invalidate();
  }

  // -- interno -------------------------------------------------------------------

  private invalidate(): void {
    this.generation++;
    const sub = this.subscription;
    this.subscription = null;
    safeRemove(sub);
  }

  private isCurrent(gen: number): boolean {
    return gen === this.generation && !this.disposed;
  }

  private setStatus(status: CaptureStatus): void {
    if (this.disposed) return;
    this.status = status;
    this.events.onStatus(status, this.mode);
  }

  /** Nunca rechaza: los errores se informan por `onError`. */
  private async activate(): Promise<void> {
    this.invalidate();
    const gen = this.generation;
    const mode = this.mode;
    if (!this.foreground) {
      this.setStatus("paused");
      return;
    }
    this.setStatus("starting");
    try {
      const source = this.sources[mode];
      const permission = await source.ensurePermission();
      if (!this.isCurrent(gen)) return;
      this.events.onPermission(permission);
      if (permission !== "granted") {
        this.wanted = false;
        this.setStatus("stopped");
        this.events.onError(permission === "blocked"
          ? "Permiso de ubicación bloqueado: actívalo en Ajustes o usa el modo simulado"
          : "Sin permiso de ubicación: concédelo o usa el modo simulado");
        return;
      }
      const sub = await source.watch((fix) => {
        if (!this.isCurrent(gen)) return;          // lectura de un inicio anulado
        this.deliver(fix, mode);
      });
      if (!this.isCurrent(gen)) {
        safeRemove(sub);                           // llegó tarde: se elimina en el acto
        return;
      }
      this.subscription = sub;
      this.setStatus("capturing");
    } catch (err) {
      if (!this.isCurrent(gen)) return;
      this.wanted = false;
      this.setStatus("stopped");
      this.events.onError(`No se pudo iniciar la ${mode === "gps" ? "ubicación GPS" : "simulación"}: ${errorText(err)}`);
    }
  }

  private deliver(fix: Fix, mode: CaptureMode): void {
    try {
      const result = this.events.onFix(fix, mode);
      if (result && typeof (result as Promise<void>).catch === "function") {
        (result as Promise<void>).catch((err) => this.events.onError(`No se pudo guardar la posición: ${errorText(err)}`));
      }
    } catch (err) {
      this.events.onError(`No se pudo guardar la posición: ${errorText(err)}`);
    }
  }
}

function safeRemove(sub: Subscription | null): void {
  try {
    sub?.remove();
  } catch {
    /* la fuente ya estaba detenida */
  }
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Fuente simulada: un intervalo que pide lecturas al generador. */
export function intervalSource(next: () => Fix, intervalMs: number): FixSource {
  return {
    ensurePermission: async () => "granted",
    watch: async (onFix) => {
      onFix(next());
      const timer = setInterval(() => onFix(next()), intervalMs);
      return { remove: () => clearInterval(timer) };
    },
  };
}
