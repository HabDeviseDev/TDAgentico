import { StatusBar } from "expo-status-bar";
import { useEffect, useState, type ReactNode } from "react";
import {
  ActivityIndicator, Pressable, SafeAreaView, ScrollView, StyleSheet, Switch, Text, TextInput, View,
} from "react-native";
import { useDriver, type CaptureState, type PermissionState } from "./src/ui/useDriver";
import { APP_CONFIG } from "./src/ui/config";
import type { OutboxRow } from "./src/core/outbox";

const permissionLabel: Record<PermissionState, string> = {
  unknown: "Sin solicitar",
  granted: "Concedido (solo con la app abierta)",
  denied: "Denegado",
  blocked: "Bloqueado: actívalo en Ajustes del sistema",
};

const captureLabel: Record<CaptureState, string> = {
  stopped: "Detenida",
  capturing: `Capturando cada ${APP_CONFIG.captureIntervalMs / 1000} s`,
  paused: "En pausa: la app está en segundo plano",
};

function time(value: string | number | null): string {
  if (value === null) return "—";
  const d = new Date(value);
  return `${d.toLocaleTimeString()} (UTC ${d.toISOString().slice(11, 19)})`;
}

export default function App() {
  const { state, actions } = useDriver();
  const [apiUrl, setApiUrl] = useState(state.settings.apiUrl);
  const [vehicleId, setVehicleId] = useState(state.settings.vehicleId);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    setApiUrl(state.settings.apiUrl);
    setVehicleId(state.settings.vehicleId);
  }, [state.settings]);

  if (state.fatalError) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.error}>{state.fatalError}</Text>
      </SafeAreaView>
    );
  }
  if (!state.ready) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator />
        <Text>Abriendo la base local…</Text>
      </SafeAreaView>
    );
  }

  const fix = state.lastFix;
  const c = state.counts;
  const capturing = state.capture !== "stopped";

  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar style="dark" />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Conductor · Monitoreo de flota</Text>
        {state.simulated && (
          <View style={styles.simBanner} accessibilityRole="alert">
            <Text style={styles.simText}>MODO SIMULADO: posiciones ficticias, no del GPS</Text>
          </View>
        )}
        {state.notice && (
          <Pressable style={styles.notice} onPress={actions.dismissNotice} accessibilityRole="button">
            <Text>{state.notice} (tocar para cerrar)</Text>
          </Pressable>
        )}

        <Section title="Configuración">
          <Text style={styles.label}>URL de la API</Text>
          <TextInput style={styles.input} value={apiUrl} onChangeText={setApiUrl} autoCapitalize="none"
                     autoCorrect={false} keyboardType="url" accessibilityLabel="URL de la API" />
          <Text style={styles.label}>Vehículo (identificador exacto, distingue mayúsculas)</Text>
          <TextInput style={styles.input} value={vehicleId} onChangeText={setVehicleId} autoCapitalize="none"
                     autoCorrect={false} maxLength={64} accessibilityLabel="Vehículo" />
          {formError && <Text style={styles.error}>{formError}</Text>}
          <Button label="Guardar configuración"
                  onPress={async () => setFormError(await actions.saveSettings(apiUrl, vehicleId))} />
          <Text style={styles.hint}>Las posiciones ya guardadas conservan el vehículo con que se capturaron.</Text>
        </Section>

        <Section title="Ubicación">
          <Row label="Permiso" value={permissionLabel[state.permission]} />
          {state.permission !== "granted" && (
            <Button label="Conceder permiso de ubicación" onPress={actions.requestPermission} />
          )}
          <View style={styles.switchRow}>
            <Text style={styles.label}>Modo simulado (sin GPS)</Text>
            <Switch value={state.simulated} onValueChange={actions.setSimulated} accessibilityLabel="Modo simulado" />
          </View>
          {state.simulated && (
            <View style={styles.switchRow}>
              <Text style={styles.label}>Simular lecturas sin velocidad</Text>
              <Switch value={state.simulateUnknownSpeed} onValueChange={actions.setSimulateUnknownSpeed}
                      accessibilityLabel="Simular lecturas sin velocidad" />
            </View>
          )}
          <Row label="Captura" value={captureLabel[state.capture]} />
          {capturing
            ? <Button label="Detener captura" onPress={actions.stopCapture} />
            : <Button label="Iniciar captura" onPress={actions.startCapture} />}
          <Text style={styles.hint}>Solo se captura con la app abierta: en segundo plano se pausa y al volver se reanuda.</Text>
        </Section>

        <Section title="Última posición">
          {fix ? (
            <>
              <Row label="Hora de captura" value={time(fix.capturedAt)} />
              <Row label="Latitud, longitud"
                   value={fix.lat !== null && fix.lon !== null ? `${fix.lat.toFixed(6)}, ${fix.lon.toFixed(6)}` : "—"} />
              <Row label="Velocidad" value={fix.speedKmh !== null ? `${fix.speedKmh.toFixed(1)} km/h` : "Desconocida"} />
              {fix.skippedReason && (
                <Text style={styles.warn}>No se envía: {fix.skippedReason}. Se guardó localmente como omitida.</Text>
              )}
              {fix.simulated && <Text style={styles.simInline}>Lectura simulada</Text>}
            </>
          ) : (
            <Text style={styles.hint}>Todavía no hay lecturas.</Text>
          )}
        </Section>

        <Section title="Sincronización">
          <Row label="Conectividad"
               value={state.online === null ? "Desconocida" : state.online ? "Con conexión" : "Sin conexión"} />
          <Row label="Pendientes de confirmar"
               value={`${c.pending + c.sending}${c.sending ? ` (${c.sending} en envío)` : ""}`} />
          <Row label="Recibidos por el servidor" value={String(c.accepted)} />
          <Row label="Rechazados" value={String(c.rejected)} />
          <Row label="Omitidos (no se envían)" value={String(c.skipped)} />
          {state.nextAttemptAt !== null && c.pending > 0 && (
            <Row label="Próximo intento" value={time(state.nextAttemptAt)} />
          )}
          {state.lastSync?.lastError && (
            <Text style={styles.warn}>Último error (se reintentará, nada se pierde): {state.lastSync.lastError}</Text>
          )}
          {state.lastSync?.skippedOffline && <Text style={styles.hint}>Sin conexión: se intentará al recuperarla.</Text>}
          <Button label={state.syncing ? "Sincronizando…" : "Sincronizar ahora"} disabled={state.syncing}
                  onPress={actions.syncNow} />
          <Text style={styles.hint}>
            "Recibido" significa que la API confirmó la publicación (Kafka). El guardado en la base del
            servidor ocurre después y se ve en el portal.
          </Text>
        </Section>

        <Section title={`Rechazados (${c.rejected})`}>
          <EventList rows={state.rejected} empty="Sin rechazos." />
        </Section>
        <Section title={`Omitidos (${c.skipped})`}>
          <EventList rows={state.skipped} empty="Sin lecturas omitidas." />
        </Section>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle} accessibilityRole="header">{title}</Text>
      {children}
    </View>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

function Button({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button"
               style={({ pressed }) => [styles.button, disabled && styles.buttonDisabled, pressed && styles.buttonPressed]}>
      <Text style={styles.buttonText}>{label}</Text>
    </Pressable>
  );
}

function EventList({ rows, empty }: { rows: OutboxRow[]; empty: string }) {
  if (rows.length === 0) return <Text style={styles.hint}>{empty}</Text>;
  return (
    <>
      {rows.map((r) => (
        <View key={r.event_id} style={styles.event}>
          <Text style={styles.mono}>{r.vehicle_id} · {r.captured_at ?? "sin fecha"}</Text>
          <Text style={styles.warn}>{r.last_error ?? "sin motivo"}</Text>
          <Text style={styles.mono}>{r.event_id}</Text>
        </View>
      ))}
    </>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#f4f6f9" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  content: { padding: 16, paddingBottom: 48, gap: 12 },
  title: { fontSize: 20, fontWeight: "700", color: "#1b2430", marginTop: 8 },
  section: { backgroundColor: "#fff", borderRadius: 10, padding: 14, borderWidth: 1, borderColor: "#dde2e8", gap: 8 },
  sectionTitle: { fontSize: 16, fontWeight: "700", color: "#1b2430" },
  label: { fontSize: 14, color: "#5b6675", flexShrink: 1 },
  input: { borderWidth: 1, borderColor: "#dde2e8", borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, fontSize: 15 },
  row: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
  rowLabel: { color: "#5b6675", fontSize: 14 },
  rowValue: { color: "#1b2430", fontSize: 14, fontWeight: "600", flexShrink: 1, textAlign: "right" },
  switchRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  button: { backgroundColor: "#1a73e8", borderRadius: 8, paddingVertical: 10, alignItems: "center" },
  buttonDisabled: { backgroundColor: "#9ab7e6" },
  buttonPressed: { opacity: 0.8 },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 15 },
  hint: { color: "#5b6675", fontSize: 13 },
  warn: { color: "#b26a00", fontSize: 13 },
  error: { color: "#c5221f", fontSize: 14 },
  notice: { backgroundColor: "#fff4e0", borderRadius: 8, padding: 10 },
  simBanner: { backgroundColor: "#b26a00", borderRadius: 8, padding: 10 },
  simText: { color: "#fff", fontWeight: "800", textAlign: "center" },
  simInline: { color: "#b26a00", fontWeight: "700", fontSize: 13 },
  event: { borderTopWidth: 1, borderTopColor: "#eceef1", paddingTop: 6, gap: 2 },
  mono: { fontFamily: "monospace", fontSize: 12, color: "#1b2430" },
});
