/**
 * Ciclo de vida de la captura con fuentes DOBLES cuya suscripción se crea con
 * demora controlada (como el GPS al inicializarse). La integración con la
 * cola usa SQLite REAL.
 */
import { randomUUID } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CaptureController, type CaptureMode, type CaptureStatus, type Fix, type FixSource, type PermissionResult,
} from "../src/core/capture";
import { buildSample } from "../src/core/contract";
import { openEnv } from "./harness";

class Deferred<T> {
  promise: Promise<T>;
  resolve!: (v: T) => void;
  reject!: (e: unknown) => void;
  constructor() {
    this.promise = new Promise<T>((res, rej) => {
      this.resolve = res;
      this.reject = rej;
    });
  }
}

/** Fuente cuya suscripción tarda hasta que la prueba la libera. */
class SlowSource implements FixSource {
  readonly pendingWatches: Deferred<void>[] = [];
  readonly pendingPermissions: Deferred<PermissionResult>[] = [];
  readonly listeners: ((fix: Fix) => void)[] = [];
  created = 0;
  removed = 0;
  holdPermission = false;
  watchError: Error | null = null;

  get active(): number {
    return this.created - this.removed;
  }

  async ensurePermission(): Promise<PermissionResult> {
    if (!this.holdPermission) return "granted";
    const d = new Deferred<PermissionResult>();
    this.pendingPermissions.push(d);
    return d.promise;
  }

  async watch(onFix: (fix: Fix) => void) {
    const d = new Deferred<void>();
    this.pendingWatches.push(d);
    await d.promise;
    if (this.watchError) throw this.watchError;
    this.created++;
    this.listeners.push(onFix);       // sigue "emitiendo" aunque se remueva (peor caso)
    let removed = false;
    return {
      remove: () => {
        if (!removed) {
          removed = true;
          this.removed++;
        }
      },
    };
  }

  /** Completa la creación de la suscripción `i` (por defecto, la más antigua pendiente). */
  async finish(i = 0) {
    this.pendingWatches[i].resolve();
    await flush();
  }

  /** Emite una lectura a TODAS las suscripciones creadas, incluidas las anuladas. */
  emit(lat = 4.65) {
    for (const l of this.listeners) l({ latitude: lat, longitude: -74.08, speedMps: 5, timestampMs: Date.now() });
  }
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function setup() {
  const gps = new SlowSource();
  const simulated = new SlowSource();
  const statuses: CaptureStatus[] = [];
  const fixes: { fix: Fix; mode: CaptureMode }[] = [];
  const errors: string[] = [];
  const controller = new CaptureController({ gps, simulated }, {
    onStatus: (s) => statuses.push(s),
    onPermission: () => {},
    onFix: (fix, mode) => {
      fixes.push({ fix, mode });
    },
    onError: (m) => errors.push(m),
  });
  return { gps, simulated, controller, statuses, fixes, errors };
}

const unhandled: unknown[] = [];
const onUnhandled = (reason: unknown) => unhandled.push(reason);
beforeEach(() => {
  unhandled.length = 0;
  process.on("unhandledRejection", onUnhandled);
});
afterEach(() => {
  process.off("unhandledRejection", onUnhandled);
  expect(unhandled).toEqual([]);           // ninguna promesa rechazada sin manejar
});

describe("inicio demorado de la suscripción", () => {
  it("detener mientras se crea la suscripción: se elimina al llegar", async () => {
    const t = setup();
    const starting = t.controller.start();
    await flush();
    expect(t.controller.currentStatus).toBe("starting");
    t.controller.stop();
    await t.gps.finish();
    await starting;
    expect(t.gps.created).toBe(1);
    expect(t.gps.active).toBe(0);
    expect(t.controller.hasSubscription).toBe(false);
    expect(t.statuses).toEqual(["starting", "stopped"]);    // nunca mostró "capturing"
    t.gps.emit();
    expect(t.fixes).toHaveLength(0);
  });

  it("detener mientras se pide el permiso: no llega a crear la suscripción", async () => {
    const t = setup();
    t.gps.holdPermission = true;
    const starting = t.controller.start();
    await flush();
    t.controller.stop();
    t.gps.pendingPermissions[0].resolve("granted");
    await starting;
    expect(t.gps.pendingWatches).toHaveLength(0);
    expect(t.gps.active).toBe(0);
    expect(t.controller.currentStatus).toBe("stopped");
  });

  it("pasar a segundo plano durante el inicio y volver", async () => {
    const t = setup();
    const starting = t.controller.start();
    await flush();
    t.controller.background();
    await t.gps.finish();
    await starting;
    expect(t.gps.active).toBe(0);
    expect(t.controller.currentStatus).toBe("paused");
    t.gps.emit();
    expect(t.fixes).toHaveLength(0);

    const resuming = t.controller.foregrounded();
    await flush();
    await t.gps.finish(1);
    await resuming;
    expect(t.gps.active).toBe(1);
    expect(t.controller.currentStatus).toBe("capturing");
    t.gps.emit();
    expect(t.fixes).toHaveLength(1);                 // solo la suscripción vigente
  });

  it("volver a primer plano no reanuda si el usuario había detenido", async () => {
    const t = setup();
    t.controller.background();
    await t.controller.foregrounded();
    expect(t.gps.pendingWatches).toHaveLength(0);
    expect(t.controller.currentStatus).toBe("stopped");
  });

  it("cambiar de GPS a simulación durante el inicio del GPS", async () => {
    const t = setup();
    const gpsStart = t.controller.start();
    await flush();
    const simStart = t.controller.setMode("simulated");
    await flush();
    await t.simulated.finish();
    await simStart;
    await t.gps.finish();                            // el GPS termina tarde
    await gpsStart;
    expect(t.gps.active).toBe(0);
    expect(t.simulated.active).toBe(1);
    expect(t.controller.currentStatus).toBe("capturing");
    t.gps.emit(1);
    t.simulated.emit(2);
    expect(t.fixes.map((f) => f.mode)).toEqual(["simulated"]);
  });

  it("inicios simultáneos antes del permiso: solo el último pide la suscripción", async () => {
    const t = setup();
    const starts = [t.controller.start(), t.controller.start(), t.controller.start()];
    await flush();
    expect(t.gps.pendingWatches).toHaveLength(1);    // los anteriores se anularon antes de `watch`
    await t.gps.finish(0);
    await Promise.all(starts);
    expect(t.gps.active).toBe(1);
    t.gps.emit();
    expect(t.fixes).toHaveLength(1);
  });

  it("inicios superpuestos con suscripciones demoradas dejan una sola y sin lecturas duplicadas", async () => {
    const t = setup();
    const starts: Promise<void>[] = [];
    for (let i = 0; i < 3; i++) {
      starts.push(t.controller.start());
      await flush();                                 // cada inicio llega a crear su suscripción
    }
    expect(t.gps.pendingWatches).toHaveLength(3);
    await t.gps.finish(2);                           // la última termina primero…
    await t.gps.finish(0);                           // …y las anteriores después
    await t.gps.finish(1);
    await Promise.all(starts);
    expect(t.gps.created).toBe(3);
    expect(t.gps.active).toBe(1);
    t.gps.emit();                                    // las tres "emiten"
    expect(t.fixes).toHaveLength(1);
    expect(t.statuses.filter((s) => s === "capturing")).toHaveLength(1);
  });

  it("desmontar durante el inicio: no queda suscripción ni cambios de estado", async () => {
    const t = setup();
    const starting = t.controller.start();
    await flush();
    const before = [...t.statuses];
    t.controller.dispose();
    await t.gps.finish();
    await starting;
    expect(t.gps.active).toBe(0);
    expect(t.statuses).toEqual(before);
    t.gps.emit();
    expect(t.fixes).toHaveLength(0);
    await t.controller.start();                      // tras desmontar no hace nada
    expect(t.gps.pendingWatches).toHaveLength(1);
  });

  it("detener, segundo plano y desmontar con captura activa sueltan la suscripción", async () => {
    for (const action of ["stop", "background", "dispose"] as const) {
      const t = setup();
      const starting = t.controller.start();
      await flush();
      await t.gps.finish();
      await starting;
      expect(t.gps.active).toBe(1);
      t.controller[action]();
      expect(t.gps.active).toBe(0);
      t.gps.emit();
      expect(t.fixes).toHaveLength(0);
    }
  });
});

describe("errores al iniciar", () => {
  it("un fallo al crear la suscripción GPS se informa sin promesas rechazadas", async () => {
    const t = setup();
    t.gps.watchError = new Error("Location services are disabled");
    const starting = t.controller.start();
    await flush();
    await t.gps.finish();
    await expect(starting).resolves.toBeUndefined();
    expect(t.controller.currentStatus).toBe("stopped");
    expect(t.errors).toEqual(["No se pudo iniciar la ubicación GPS: Location services are disabled"]);
  });

  it("permiso denegado detiene con un mensaje", async () => {
    const t = setup();
    t.gps.holdPermission = true;
    const starting = t.controller.start();
    await flush();
    t.gps.pendingPermissions[0].resolve("blocked");
    await starting;
    expect(t.controller.currentStatus).toBe("stopped");
    expect(t.errors[0]).toContain("bloqueado");
    expect(t.gps.pendingWatches).toHaveLength(0);
  });

  it("un error al guardar una lectura se informa y la captura sigue", async () => {
    const gps = new SlowSource();
    const errors: string[] = [];
    const controller = new CaptureController({ gps, simulated: new SlowSource() }, {
      onStatus: () => {}, onPermission: () => {}, onError: (m) => errors.push(m),
      onFix: async () => {
        throw new Error("disco lleno");
      },
    });
    const starting = controller.start();
    await flush();
    await gps.finish();
    await starting;
    gps.emit();
    await flush();
    expect(errors).toEqual(["No se pudo guardar la posición: disco lleno"]);
    expect(controller.currentStatus).toBe("capturing");
  });
});

describe("integración con la cola SQLite", () => {
  it("solo el inicio vigente guarda lecturas y detener conserva lo guardado", async () => {
    const env = await openEnv();
    const gps = new SlowSource();
    const controller = new CaptureController({ gps, simulated: new SlowSource() }, {
      onStatus: () => {}, onPermission: () => {}, onError: () => {},
      onFix: async (fix) => {
        await env.outbox.record(buildSample({ eventId: randomUUID(), vehicleId: "CAP-1", ...fix }), false);
      },
    });
    const first = controller.start();
    await flush();
    const second = controller.start();
    await flush();
    await gps.finish(0);
    await gps.finish(1);
    await Promise.all([first, second]);
    gps.emit(4.61);
    gps.emit(4.62);
    await new Promise((r) => setTimeout(r, 50));
    expect((await env.outbox.counts()).pending).toBe(2);   // no 4: una sola suscripción vigente

    controller.stop();
    gps.emit(4.63);
    await new Promise((r) => setTimeout(r, 50));
    expect(gps.active).toBe(0);
    const rows = await env.db.all<{ lat: number }>("SELECT lat FROM outbox ORDER BY id");
    expect(rows.map((r) => r.lat)).toEqual([4.61, 4.62]);  // lo guardado se conserva
    await env.db.close();
  });
});
