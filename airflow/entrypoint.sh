#!/bin/bash
set -e

# Remove PID files left by a previous run of this container
rm -f /opt/airflow/airflow-*.pid

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

# Start the API server, the scheduler and the DAG processor. The container stops if any of them does.
echo "Starting Airflow API server, scheduler and DAG processor..."
airflow api-server --port 8080 &
airflow scheduler &
airflow dag-processor &
wait -n
