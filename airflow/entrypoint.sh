#!/bin/bash
set -e

# Remove PID files left by a previous run of this container
rm -f /opt/airflow/airflow-webserver.pid
rm -f /opt/airflow/airflow-scheduler.pid

# Initialize Airflow database
echo "Initializing Airflow database..."
airflow db migrate

# Create the admin user with the generated password
echo "Creating admin user..."
airflow users create \
    --username admin \
    --firstname Admin \
    --lastname User \
    --role Admin \
    --email admin@example.com \
    --password "$AIRFLOW_ADMIN_PASSWORD" || echo "Admin user already exists"

# Start webserver and scheduler
echo "Starting Airflow webserver and scheduler..."
airflow webserver --port 8080 --daemon &
airflow scheduler
