"""Every 15 minutes: land new call events in the bronze layer, then check it.

The DAG orchestrates; Spark computes. Both tasks are thin Spark Connect
clients, so this container never holds the data.

Why these settings (DDIA.md §7, §15):
  - max_active_runs=1: the streaming checkpoint allows ONE writer. Two runs
    racing on it is the concurrent-writer problem, so Airflow serialises them.
  - catchup=False: progress lives in the Kafka offsets in the checkpoint, not
    in the run's date interval. A run "for 09:00" reads whatever is new, so
    backfilling old intervals would just be empty runs.
"""

import pendulum
from airflow.sdk import dag, task


@dag(
    schedule="*/15 * * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["lake", "bronze"],
)
def lake_bronze():

    @task(retries=2, retry_delay=pendulum.duration(minutes=1))
    def ingest_call_events() -> dict:
        from jobs.bronze_call_events import connect, ingest
        return ingest(connect())

    @task
    def check_bronze(stats: dict) -> dict:
        """Fail on what must never happen; report what is merely expected."""
        from pyspark.sql import functions as F
        from jobs.bronze_call_events import BRONZE, connect

        df = connect().read.format("delta").load(BRONZE)
        null_keys = df.where(F.col("call_id").isNull() | (F.col("call_id") == "")
                             | F.col("event_id").isNull()).count()
        # Duplicates are EXPECTED in bronze — delivery is at-least-once, and
        # bronze keeps what arrived. Silver dedupes on event_id. Tracked, not failed.
        dupes = df.count() - df.select("event_id").distinct().count()
        result = {**stats, "null_keys": null_keys, "duplicate_event_ids": dupes}
        print(result)
        if null_keys:
            raise ValueError(f"{null_keys} bronze rows without call_id/event_id")
        return result

    check_bronze(ingest_call_events())


lake_bronze()
