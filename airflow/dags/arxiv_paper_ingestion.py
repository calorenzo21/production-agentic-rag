from datetime import datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, CronTriggerTimetable

# Import task functions from separate module
from arxiv_ingestion.tasks import (
    cleanup_temp_files,
    fetch_daily_papers,
    generate_daily_report,
    index_papers_to_opensearch,
    setup_environment,
)

# Default DAG arguments
default_args = {
    "owner": "arxiv-curator",
    "depends_on_past": False,
    "start_date": datetime(2025, 8, 8),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=30),
    "catchup": False,
}

# Create the DAG
dag = DAG(
    "arxiv_paper_ingestion",
    default_args=default_args,
    description="Daily arXiv CS.AI paper pipeline: fetch → store to PostgreSQL → index to OpenSearch",
    # Every day at 6 AM UTC, weekends included. A trigger timetable makes a run's logical date the day it runs,
    # and each run ingests the day four days before, the most recent day arXiv has always finished announcing (see
    # ANNOUNCEMENT_LAG). Missed days are recovered with `airflow backfill create`, whose run of a day also ingests
    # four days before: recovering the 17th means backfilling the 21st.
    schedule=CronTriggerTimetable("0 6 * * *", timezone="UTC"),
    max_active_runs=1,
    catchup=False,
    tags=["arxiv", "papers", "ingestion", "opensearch", "week3"],
)

# Task definitions
setup_task = PythonOperator(
    task_id="setup_environment",
    python_callable=setup_environment,
    dag=dag,
)

fetch_task = PythonOperator(
    task_id="fetch_daily_papers",
    python_callable=fetch_daily_papers,
    dag=dag,
)

# Indexes the Papers fetch_task stored, by the arXiv IDs it passes on through XCom.
opensearch_task = PythonOperator(
    task_id="index_papers_to_opensearch",
    python_callable=index_papers_to_opensearch,
    dag=dag,
)

report_task = PythonOperator(
    task_id="generate_daily_report",
    python_callable=generate_daily_report,
    dag=dag,
)

# Deletes PDFs older than 30 days from the folder the arXiv client downloads them into.
cleanup_task = PythonOperator(
    task_id="cleanup_temp_files",
    python_callable=cleanup_temp_files,
    dag=dag,
)

# Task dependencies
# Main pipeline: setup -> fetch -> opensearch -> report -> cleanup
setup_task >> fetch_task >> opensearch_task >> report_task >> cleanup_task
