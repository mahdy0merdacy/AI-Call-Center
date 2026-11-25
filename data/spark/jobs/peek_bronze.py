"""Show what landed in the lake: row count, columns, a few rows of bronze.

    SPARK_REMOTE=sc://localhost:15002 .venv/bin/python data/spark/jobs/peek_bronze.py
"""
import os

from pyspark.sql import SparkSession

s = SparkSession.builder.remote(os.environ.get("SPARK_REMOTE", "sc://localhost:15002")).getOrCreate()
for path in ("s3a://lake/bronze/call_events", "s3a://lake/bronze/call_events_quarantine"):
    try:
        df = s.read.format("delta").load(path)
    except Exception as exc:  # quarantine may not exist until a bad record arrives
        print(f"{path}: not readable ({type(exc).__name__})")
        continue
    print(f"\n{path}: {df.count()} rows")
    df.show(5, truncate=25)
