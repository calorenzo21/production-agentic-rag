# Airflow 3, to share SQLAlchemy 2 with the application

Airflow tasks import the application's code from `src/`, but Airflow 2 caps SQLAlchemy below 2.0 while the application uses 2.0. Lint and type checks run against one version and Airflow runs the other, so code can pass CI and still break in a DAG. So this repository uses Airflow 3 with the same SQLAlchemy version as the application, although the course stays on Airflow 2 and ADR 0002 makes the course's versions the default.

## Considered Options

- **Stay on Airflow 2 and write code that works with both versions**: rejected because it keeps the application on the older API and only `make smoke` would catch a mistake.
- **Move the application down to SQLAlchemy 1.4**: rejected because it gives up the current API for the sake of one service.
- **Isolate Airflow from `src/`**: already rejected in ADR 0002, because the course's ingestion DAGs write Papers directly to the application database.

## Consequences

DAGs from later Weeks of the course will not work unchanged: they need Airflow 3's imports and the removal of what Airflow 3 dropped. Airflow's constraints set the lowest SQLAlchemy version the application can use, and `make smoke` fails if the two versions differ.
