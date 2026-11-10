-- Registro persistente de event_id repetidos con contenido distinto.
-- El evento original se conserva en ingested_events/position_history;
-- aquí queda cada mensaje conflictivo recibido.
-- Idempotente: se puede aplicar sobre una base existente.

CREATE TABLE IF NOT EXISTS event_id_conflicts (
    event_id        uuid        NOT NULL REFERENCES ingested_events (event_id),
    original_hash   bytea       NOT NULL,
    received_hash   bytea       NOT NULL,
    kafka_topic     text        NOT NULL,
    kafka_partition integer     NOT NULL,
    kafka_offset    bigint      NOT NULL,
    detected_at     timestamptz NOT NULL DEFAULT now(),
    -- Un mensaje de Kafka se identifica por (tópico, partición, offset):
    -- reprocesarlo choca con esta clave y no duplica el registro.
    PRIMARY KEY (kafka_topic, kafka_partition, kafka_offset)
);

CREATE INDEX IF NOT EXISTS event_id_conflicts_event_id_idx
    ON event_id_conflicts (event_id);
