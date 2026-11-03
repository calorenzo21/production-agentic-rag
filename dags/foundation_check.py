from airflow.sdk import dag, task


@dag(dag_id="foundation_check", schedule=None, catchup=False)
def foundation_check() -> None:
    """Prove that scheduling and execution work before Paper ingestion needs them."""

    @task
    def confirm() -> str:
        return "ok"

    confirm()


foundation_check()
