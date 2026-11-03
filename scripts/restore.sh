#!/bin/sh
# Restores a backup made by backup.sh, stopping everything that writes to the databases meanwhile.
set -eu

directory="${1:?usage: restore.sh <backup directory>}"
for database in paper_curator airflow; do
    [ -f "$directory/$database.dump" ] || { echo "Missing $directory/$database.dump" >&2; exit 1; }
done

directory="$(cd "$directory" && pwd)"
cd "$(dirname "$0")/.."

docker compose stop api airflow-apiserver airflow-scheduler airflow-dag-processor airflow-triggerer

for database in paper_curator airflow; do
    docker compose exec -T --user postgres postgres pg_restore --username=postgres --clean --if-exists \
        --single-transaction --dbname="$database" <"$directory/$database.dump"
done

docker compose up --detach --wait
