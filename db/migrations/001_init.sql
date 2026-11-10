-- Esquema de ingesta de posiciones (etapa 1).
-- Se aplica automáticamente la primera vez que arranca el volumen de TimescaleDB.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1) Clave de deduplicación. Tabla normal (no hypertable) para poder tener
--    unicidad global por event_id; en una hypertable todo índice único debe
--    incluir la columna de partición.
CREATE TABLE IF NOT EXISTS ingested_events (
    event_id        uuid        PRIMARY KEY,
    vehicle_id      text        NOT NULL,
    captured_at     timestamptz NOT NULL,
    payload_hash    bytea       NOT NULL,
    kafka_topic     text        NOT NULL,
    kafka_partition integer     NOT NULL,
    kafka_offset    bigint      NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

-- 2) Historial completo, incluidos los eventos atrasados.
CREATE TABLE IF NOT EXISTS position_history (
    event_id     uuid             NOT NULL,
    vehicle_id   text             NOT NULL,
    captured_at  timestamptz      NOT NULL,
    lat          double precision NOT NULL,
    lon          double precision NOT NULL,
    speed_kmh    real             NOT NULL,
    ingested_at  timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, captured_at, event_id)
);

SELECT create_hypertable(
    'position_history',
    by_range('captured_at', INTERVAL '1 day'),
    if_not_exists => TRUE
);

-- 3) Posición visible: una fila por vehículo con el dato más reciente
--    según el orden (captured_at, event_id).
CREATE TABLE IF NOT EXISTS vehicle_current_position (
    vehicle_id   text             PRIMARY KEY,
    event_id     uuid             NOT NULL,
    captured_at  timestamptz      NOT NULL,
    lat          double precision NOT NULL,
    lon          double precision NOT NULL,
    speed_kmh    real             NOT NULL,
    updated_at   timestamptz      NOT NULL DEFAULT now()
);
