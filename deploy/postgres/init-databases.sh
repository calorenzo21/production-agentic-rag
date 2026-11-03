#!/bin/sh
# Runs once, on an empty data directory: one database and one user for each owner of data.
set -eu

read_secret() {
    file_var="$1_FILE"
    eval "file=\${$file_var:-}"
    if [ -n "$file" ]; then
        cat "$file"
    else
        eval "printf '%s' \"\${$1:?$1 or $file_var is required}\""
    fi
}

create_database() {
    psql --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname=postgres \
        --set=owner="$1" --set=database="$2" --set=password="$3" <<'SQL'
CREATE ROLE :"owner" LOGIN PASSWORD :'password';
CREATE DATABASE :"database" OWNER :"owner";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
SQL
}

create_database curator paper_curator "$(read_secret CURATOR_DB_PASSWORD)"
create_database airflow airflow "$(read_secret AIRFLOW_DB_PASSWORD)"
