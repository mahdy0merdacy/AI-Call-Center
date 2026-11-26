"""Every 15 minutes: Kafka -> bronze -> silver -> gold, checked between each layer.

    ingest_bronze ─▶ bronze_gate ─▶ build_silver ─▶ silver_checks ─▶ build_gold ─▶ gold_checks
                     (GX)                           (GX)                            (GX)
        ─▶ publish_insights   gold -> Postgres analytics.*, for the portal's Insights page

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
subprocess, so its dependencies never meet Airflow's. publish_insights
(data/serving/publish.py) runs in the same venv (delta-rs, pandas, psycopg) and
only after gold_checks passed: a client never sees gold that failed its gate.
"""

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task

# The run's date. Airflow 3 starts MANUAL runs without a logical date, so `ds`
# and `ts_nodash` are undefined there and every templated task failed before it
# ran. run_after exists on every run (scheduled: its interval end; manual: now).
RUN_DATE = "(dag_run.logical_date or dag_run.run_after)"
SINCE = "{{ macros.ds_add(" + RUN_DATE + " | ds, -1) }}"
QUALITY = ("/opt/quality-venv/bin/python /opt/aicc/quality/run.py --since " + SINCE
           + " --json /opt/airflow/quality/results/{{ " + RUN_DATE
           + " | ts_nodash }}-{{ task.task_id }}.json")
PUBLISH = ("/opt/quality-venv/bin/python /opt/aicc/serving/publish.py --since " + SINCE
           + " --run-id '{{ run_id }}'")


def since_day() -> str:
    """The day before the run's date, for the Python tasks (see RUN_DATE)."""
    from airflow.sdk import get_current_context
    run = get_current_context()["dag_run"]
    when = pendulum.instance(run.logical_date or run.run_after)
    return when.subtract(days=1).to_date_string()


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
    def build_silver() -> dict:
        from jobs.bronze_call_events import connect
        from jobs.silver_calls import run
        since = since_day()
        return run(connect(), since)

    silver_checks = BashOperator(
        task_id="silver_checks",
        bash_command=f"{QUALITY} silver_call_events silver_calls silver_turns "
                     "silver_utterance_emotions silver_call_sentiment silver_knowledge_searches",
    )

    @task(retries=1, retry_delay=pendulum.duration(minutes=1))
    def build_gold() -> dict:
        from jobs import gold_insights, gold_tenant_daily
        from jobs.bronze_call_events import connect
        since = since_day()
        spark = connect()
        return {"tenant_daily": gold_tenant_daily.run(spark, since),
                "insights": gold_insights.run(spark, since)}

    gold_checks = BashOperator(
        task_id="gold_checks",
        bash_command=f"{QUALITY} gold_tenant_daily gold_sentiment_daily gold_call_sentiment_daily "
                     "gold_tool_daily gold_knowledge_daily gold_document_usage "
                     "gold_knowledge_gaps gold_hourly gold_calls_to_review",
    )

    publish_insights = BashOperator(
        task_id="publish_insights",
        bash_command=PUBLISH,
        retries=2,
        retry_delay=pendulum.duration(minutes=1),
    )

    (ingest_bronze() >> bronze_gate >> build_silver() >> silver_checks
     >> build_gold() >> gold_checks >> publish_insights)


lake_pipeline()
