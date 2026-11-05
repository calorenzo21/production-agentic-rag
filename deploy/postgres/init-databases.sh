#!/bin/sh
# Runs once, on an empty data directory: one database and one user for each owner of data.
set -eu

create_database() {
    psql --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname=postgres \
        --set=owner="$1" --set=database="$2" --set=password="$3" <<'SQL'
CREATE ROLE :"owner" LOGIN PASSWORD :'password';
CREATE DATABASE :"database" OWNER :"owner";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
SQL
}

create_database rag_user rag_db "$RAG_DB_PASSWORD"
create_database airflow airflow "$AIRFLOW_DB_PASSWORD"
