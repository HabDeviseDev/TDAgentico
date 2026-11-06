#!/bin/sh
# Aplica db/migrations/*.sql en orden alfabético.
# Cada archivo y su registro en schema_migrations van en una misma
# transacción: si la migración falla, no queda marcada como aplicada.
# Las migraciones son idempotentes, así que también sirven sobre una base
# creada antes de existir schema_migrations.
set -eu

psql -v ON_ERROR_STOP=1 -q -c "
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);"

for file in /db/migrations/*.sql; do
    version=$(basename "$file" .sql)
    applied=$(psql -tA -c "SELECT 1 FROM schema_migrations WHERE version = '$version'")
    if [ "$applied" = "1" ]; then
        echo "= $version (ya aplicada)"
        continue
    fi
    echo "+ $version"
    psql -v ON_ERROR_STOP=1 -q --single-transaction \
        -f "$file" \
        -c "INSERT INTO schema_migrations (version) VALUES ('$version')"
done

echo "Migraciones al día"
