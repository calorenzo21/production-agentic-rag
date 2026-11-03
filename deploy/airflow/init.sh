#!/bin/sh
# Prepares Airflow's database and its admin user. Safe to run on every start.
set -eu

airflow db migrate

if [ -n "${AIRFLOW_ADMIN_PASSWORD_FILE:-}" ]; then
    AIRFLOW_ADMIN_PASSWORD="$(cat "$AIRFLOW_ADMIN_PASSWORD_FILE")"
fi

if ! airflow users list --output plain | grep -qw admin; then
    airflow users create --username admin --password "${AIRFLOW_ADMIN_PASSWORD:?}" \
        --firstname Paper --lastname Curator --role Admin --email admin@paper-curator.invalid
fi
