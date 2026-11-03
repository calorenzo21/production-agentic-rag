#!/bin/sh
# Backs up the application and Airflow databases and prints the backup's directory.
# OpenSearch is left out on purpose: its indexes are rebuilt from PostgreSQL.
set -eu

cd "$(dirname "$0")/.."
umask 077
directory="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$directory"

for database in paper_curator airflow; do
    docker compose exec -T --user postgres postgres pg_dump --username=postgres --format=custom \
        --dbname="$database" >"$directory/$database.dump"
done

echo "$directory"
