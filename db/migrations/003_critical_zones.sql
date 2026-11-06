-- Zonas críticas (círculos) y zonas de demostración alrededor de Bogotá.
-- Idempotente: se puede aplicar sobre una base existente y volver a aplicar.

CREATE TABLE IF NOT EXISTS critical_zones (
    zone_id     text             PRIMARY KEY,
    name        text             NOT NULL,
    center_lat  double precision NOT NULL CHECK (center_lat BETWEEN -90 AND 90),
    center_lon  double precision NOT NULL CHECK (center_lon BETWEEN -180 AND 180),
    radius_m    double precision NOT NULL CHECK (radius_m > 0 AND radius_m <= 50000),
    active      boolean          NOT NULL DEFAULT true,
    -- Datos ficticios de demostración (se muestran como tales en el portal).
    is_demo     boolean          NOT NULL DEFAULT false,
    created_at  timestamptz      NOT NULL DEFAULT now()
);

-- Ubicaciones aproximadas y ficticias: no representan zonas reales de riesgo.
-- DEMO-Z02 y DEMO-Z03 se superponen a propósito.
-- ON CONFLICT DO NOTHING: no pisa cambios hechos después (p. ej. desactivar).
INSERT INTO critical_zones (zone_id, name, center_lat, center_lon, radius_m, active, is_demo)
VALUES
    ('DEMO-Z01', 'Demo · Centro logístico Puente Aranda', 4.6230, -74.1150, 400, true,  true),
    ('DEMO-Z02', 'Demo · Terminal Salitre',               4.6545, -74.1155, 350, true,  true),
    ('DEMO-Z03', 'Demo · Corredor Avenida La Esperanza',  4.6560, -74.1110, 300, true,  true),
    ('DEMO-Z04', 'Demo · Patio Usaquén',                  4.7020, -74.0300, 250, true,  true),
    ('DEMO-Z05', 'Demo · Zona inactiva Kennedy',          4.6280, -74.1500, 300, false, true)
ON CONFLICT (zone_id) DO NOTHING;
