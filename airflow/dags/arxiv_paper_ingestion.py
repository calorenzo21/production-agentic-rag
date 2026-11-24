from datetime import datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, CronTriggerTimetable, TriggerRule

# Import task functions from separate module
from arxiv_ingestion.tasks import (
    cleanup_temp_files,
    fail_the_run,
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

# Indexes the Papers fetch_task stored, by the arXiv IDs it passes on through XCom. It fails when the indexing does, so
# that Airflow retries it, twice and 30 minutes apart.
opensearch_task = PythonOperator(
    task_id="index_papers_to_opensearch",
    python_callable=index_papers_to_opensearch,
    dag=dag,
)

# Runs once the indexing has succeeded or failed, after its last retry, so that it shows a failed indexing too. When the
# Ingestion fails, the indexing does not run, nor does the report.
report_task = PythonOperator(
    task_id="generate_daily_report",
    python_callable=generate_daily_report,
    trigger_rule=TriggerRule.ONE_DONE,
    dag=dag,
)

# Deletes PDFs older than 30 days from the folder the arXiv client downloads them into. It runs whatever happened before
# it, so that no failure lets the folder grow.
cleanup_task = PythonOperator(
    task_id="cleanup_temp_files",
    python_callable=cleanup_temp_files,
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

# Airflow's watcher: it runs only once another task has failed, and fails the run. A run takes its state from its last
# tasks, here the cleanup, which runs anyway, so without it a run would succeed after a failed indexing. When no task
# fails, it is skipped and the run succeeds.
watcher_task = PythonOperator(
    task_id="watcher",
    python_callable=fail_the_run,
    trigger_rule=TriggerRule.ONE_FAILED,
    retries=0,
    dag=dag,
)

# Task dependencies
# Main pipeline: setup -> fetch -> opensearch -> report -> cleanup, and the watcher after every task
setup_task >> fetch_task >> opensearch_task >> report_task >> cleanup_task
[setup_task, fetch_task, opensearch_task, report_task, cleanup_task] >> watcher_task
