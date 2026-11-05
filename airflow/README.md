# Airflow Configuration

This directory contains Apache Airflow configuration and DAGs for the arXiv Paper Curator project.

## Week 1 Setup

For Week 1, we have a basic setup with:

- **hello_world_dag.py**: Simple test DAG to verify Airflow is working

## Directory Structure

```
airflow/
├── README.md           # This file
├── Dockerfile
├── entrypoint.sh
├── requirements-airflow.txt
└── dags/
    └── hello_world_dag.py  # Test DAG for Week 1
```

## Usage

The Airflow service is configured to run via Docker Compose and can be accessed at:
- Web UI: http://localhost:8080
- Username: `admin`
- Password: the value of `AIRFLOW_ADMIN_PASSWORD` in `.env`

Airflow keeps its metadata in its own `airflow` database. DAGs reach the application's `rag_db` through `POSTGRES_DATABASE_URL`.

## Future Weeks

In later weeks, this directory will contain:
- arXiv paper fetching DAGs
- PDF processing workflows
- Data pipeline orchestration
