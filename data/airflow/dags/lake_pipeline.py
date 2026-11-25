"""Every 15 minutes: Kafka -> bronze -> silver -> gold, checked between each layer.

    ingest_bronze ─▶ bronze_gate ─▶ build_silver ─▶ silver_checks ─▶ build_gold ─▶ gold_checks
                     (GX)                           (GX)                            (GX)

The DAG orchestrates; Spark computes; Great Expectations judges.

Gates: each GX task exits non-zero when a CRITICAL rule fails, which fails the
task, so nothing downstream runs on bad data. Warnings are logged and kept in
the Data Docs report, and do not stop the pipeline. The rules are in
data/quality/suites.py; the reasoning behind each in DDIA.md.

Why these settings (DDIA.md §7, §15):
  - max_active_runs=1: the bronze streaming checkpoint allows ONE writer, and
    two silver MERGEs into the same table would conflict. Airflow serialises.
  - catchup=False: bronze progress lives in the Kafka offsets of its
    checkpoint, not in the run's interval, so backfilling old intervals would
    be empty runs. Silver is a MERGE, so re-running any window is harmless.
  - Windows start the day BEFORE the run's date: a run just after midnight
    still covers events ingested late the previous day.

GX runs in its own virtualenv (/opt/quality-venv, see airflow/Dockerfile) as a
subprocess, so its dependencies never meet Airflow's.
"""

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task

SINCE = "{{ macros.ds_add(ds, -1) }}"
QUALITY = ("/opt/quality-venv/bin/python /opt/aicc/quality/run.py --since " + SINCE
           + " --json /opt/airflow/quality/results/{{ ts_nodash }}-{{ task.task_id }}.json")


@dag(
    schedule="*/15 * * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["lake", "bronze", "silver", "quality"],
)
def lake_pipeline():

    @task(retries=2, retry_delay=pendulum.duration(minutes=1))
    def ingest_bronze() -> dict:
        from jobs.bronze_call_events import connect, ingest
        return ingest(connect())

    bronze_gate = BashOperator(
        task_id="bronze_gate",
        bash_command="mkdir -p /opt/airflow/quality/results && "
                     f"{QUALITY} bronze bronze_batch_stats",
    )

    @task(retries=1, retry_delay=pendulum.duration(minutes=1))
    def build_silver(ds: str | None = None) -> dict:
        from jobs.bronze_call_events import connect
        from jobs.silver_calls import run
        since = pendulum.parse(ds).subtract(days=1).to_date_string()
        return run(connect(), since)

    silver_checks = BashOperator(
        task_id="silver_checks",
        bash_command=f"{QUALITY} silver_call_events silver_calls silver_turns",
    )

    @task(retries=1, retry_delay=pendulum.duration(minutes=1))
    def build_gold(ds: str | None = None) -> dict:
        from jobs.bronze_call_events import connect
        from jobs.gold_tenant_daily import run
        since = pendulum.parse(ds).subtract(days=1).to_date_string()
        return run(connect(), since)

    gold_checks = BashOperator(
        task_id="gold_checks",
        bash_command=f"{QUALITY} gold_tenant_daily",
    )

    (ingest_bronze() >> bronze_gate >> build_silver() >> silver_checks
     >> build_gold() >> gold_checks)


lake_pipeline()
