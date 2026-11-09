import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatResponse } from "../api/chat";
import type { ChatEntry } from "../hooks/useChat";
import { useChat } from "../hooks/useChat";
import {
  EXAMPLE_QUESTIONS, chatStatusLabel, selectableVehicles, sourceTime, toolLabel, toolStatusLabel,
  warningSeverity,
} from "../lib/chatFormat";
import { ChatMarkdown } from "./ChatMarkdown";

interface Props {
  open: boolean;
  onClose: () => void;
  selectedId: string | null;
  onSelectVehicle: (vehicleId: string) => void;
}

/**
 * Panel de chat con el agente operativo. Todo el texto (respuesta del modelo
 * y datos de herramientas) se muestra como texto de React, nunca como HTML.
 */
export function ChatPanel({ open, onClose, selectedId, onSelectVehicle }: Props) {
  const chat = useChat();
  const [draft, setDraft] = useState("");
  const historyEnd = useRef<HTMLDivElement>(null);
  const configured = chat.service?.configured ?? false;
  const canSend = configured && !chat.pending && draft.trim().length > 0;

  useEffect(() => {
    historyEnd.current?.scrollIntoView({ block: "end" });
  }, [chat.entries]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (canSend && chat.send(draft)) setDraft("");
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <aside className={`chat-drawer${open ? " open" : ""}`} aria-label="Asistente operativo" aria-hidden={!open}>
      <header className="chat-header">
        <div>
          <h2>Asistente operativo</h2>
          <p className="muted small">
            Cada pregunta se responde de forma independiente; el historial es solo visual.
          </p>
        </div>
        <button type="button" className="link-button" onClick={onClose}>
          Cerrar
        </button>
      </header>

      {chat.serviceError && (
        <p className="notice notice-error" role="alert">{chat.serviceError}</p>
      )}
      {chat.service && !chat.service.configured && (
        <p className="notice notice-warn" role="alert">
          El agente requiere configuración: {chat.service.problem ?? "falta el proveedor"}. Define la
          credencial en el archivo <code>.env</code> del servidor (ver README).
        </p>
      )}

      <div className="chat-history" aria-live="polite">
        {chat.entries.length === 0 && (
          <p className="muted small chat-empty">
            Pregunta por alertas de detención, zonas activas, posiciones o datos desactualizados.
          </p>
        )}
        {chat.entries.map((entry) => (
          <ChatItem
            key={entry.id}
            entry={entry}
            disabled={chat.pending}
            selectedId={selectedId}
            onRetry={() => chat.retry(entry.id)}
            onSelectVehicle={onSelectVehicle}
          />
        ))}
        <div ref={historyEnd} />
      </div>

      <div className="chat-examples" aria-label="Preguntas de ejemplo">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            className="chip"
            disabled={!configured || chat.pending}
            onClick={() => chat.send(q)}
          >
            {q}
          </button>
        ))}
      </div>

      <form className="chat-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="chat-question">Pregunta</label>
        <textarea
          id="chat-question"
          rows={2}
          value={draft}
          maxLength={chat.maxChars}
          placeholder={configured ? "Escribe una pregunta…" : "El agente no está configurado"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={!configured}
        />
        <div className="chat-form-row">
          <span className="muted small">
            {draft.length}/{chat.maxChars}
          </span>
          <button type="submit" className="primary-button" disabled={!canSend}>
            {chat.pending ? "Consultando…" : "Enviar"}
          </button>
        </div>
      </form>
    </aside>
  );
}

function ChatItem({ entry, disabled, selectedId, onRetry, onSelectVehicle }: {
  entry: ChatEntry;
  disabled: boolean;
  selectedId: string | null;
  onRetry: () => void;
  onSelectVehicle: (id: string) => void;
}) {
  return (
    <div className="chat-item">
      <p className="chat-question">{entry.question}</p>
      <div className={`chat-reply chat-reply-${entry.state}`}>
        {entry.state === "pending" && (
          <p className="muted small">
            <span className="spinner spinner-inline" aria-hidden="true" /> Consultando al agente…
          </p>
        )}
        {entry.state === "error" && (
          <div className="notice notice-error" role="alert">
            <p>{entry.error}</p>
            <button type="button" className="secondary-button" onClick={onRetry} disabled={disabled}>
              Reintentar
            </button>
          </div>
        )}
        {entry.response && (
          <ResponseView response={entry.response} selectedId={selectedId} onSelectVehicle={onSelectVehicle} />
        )}
      </div>
    </div>
  );
}

function ResponseView({ response, selectedId, onSelectVehicle }: {
  response: ChatResponse;
  selectedId: string | null;
  onSelectVehicle: (id: string) => void;
}) {
  const statusLabel = chatStatusLabel(response.status);
  const vehicles = selectableVehicles(response);
  const ev = response.evidence;
  return (
    <>
      {statusLabel && <span className="badge-status">{statusLabel}</span>}
      {response.answer && <ChatMarkdown className="chat-answer" text={response.answer} />}

      {response.warnings.length > 0 && (
        <ul className="chat-warnings">
          {response.warnings.map((w, i) => (
            <li key={`${w.code}-${i}`} className={`chat-warning chat-warning-${warningSeverity(w.code)}`}>
              {w.message}
            </li>
          ))}
        </ul>
      )}

      {vehicles.shown.length > 0 && (
        <div className="chat-vehicles" aria-label="Vehículos de la respuesta">
          {vehicles.shown.map((id) => (
            <button
              key={id}
              type="button"
              className={`chip chip-vehicle${id === selectedId ? " selected" : ""}`}
              aria-pressed={id === selectedId}
              onClick={() => onSelectVehicle(id)}
              title="Seleccionar en el mapa"
            >
              {id}
            </button>
          ))}
          {vehicles.hidden > 0 && <span className="muted small">y {vehicles.hidden} más</span>}
        </div>
      )}
      {response.zone_ids.length > 0 && (
        <p className="muted small">Zonas: {response.zone_ids.join(", ")}</p>
      )}

      {response.sources.length > 0 && (
        <ul className="chat-sources muted small">
          {response.sources.map((s, i) => {
            const at = sourceTime(s);
            return (
              <li key={`${s.tool}-${i}`}>
                {toolLabel(s.tool)}: {toolStatusLabel(s.status)}
                {at && <> · <UtcTime utc={at.utc} text={at.label} /></>}
              </li>
            );
          })}
        </ul>
      )}

      {(ev.alerts.length > 0 || ev.insufficient_evidence.length > 0 || ev.vehicles.length > 0 || ev.zones.length > 0) && (
        <details className="chat-evidence">
          <summary>Evidencias</summary>
          {ev.alerts.length > 0 && (
            <ul>
              {ev.alerts.map((a) => (
                <li key={a.alert_id}>
                  {a.vehicle_id} en {a.zone_name}: {a.start_unknown ? "≥ " : ""}
                  {a.observed_duration_min} min · última muestra{" "}
                  <UtcTime utc={a.last_sample_at_utc} text={a.last_sample_at_local} />
                </li>
              ))}
            </ul>
          )}
          {ev.insufficient_evidence.length > 0 && (
            <ul>
              {ev.insufficient_evidence.map((i) => (
                <li key={i.vehicle_id}>
                  {i.vehicle_id}: evidencia insuficiente ({i.reason})
                  {i.last_sample_at_utc && i.last_sample_at_local && (
                    <> · última muestra <UtcTime utc={i.last_sample_at_utc} text={i.last_sample_at_local} /></>
                  )}
                </li>
              ))}
            </ul>
          )}
          {ev.vehicles.length > 0 && (
            <ul>
              {ev.vehicles.map((v) => (
                <li key={v.vehicle_id}>
                  {v.vehicle_id}: {v.lat.toFixed(5)}, {v.lon.toFixed(5)} · {v.speed_kmh} km/h ·{" "}
                  <UtcTime utc={v.captured_at_utc} text={v.captured_at_local} />
                  {v.stale ? " · desactualizado" : ""}
                </li>
              ))}
            </ul>
          )}
          {ev.zones.length > 0 && (
            <ul>
              {ev.zones.map((z) => (
                <li key={z.zone_id}>
                  {z.zone_id}: {z.name} · {z.radius_m} m{z.is_demo ? " · demostración" : ""}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
      <p className="muted small chat-meta">
        {response.usage.tool_calls} consulta(s) · {(response.usage.duration_ms / 1000).toFixed(1)} s · {response.model}
      </p>
    </>
  );
}

/** Hora local calculada por el agente; el UTC original queda en dateTime y en el título. */
function UtcTime({ utc, text }: { utc: string; text: string }) {
  return <time dateTime={utc} title={`UTC: ${utc}`}>{text}</time>;
}
