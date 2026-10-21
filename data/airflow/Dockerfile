# Airflow + the Spark Connect thin client. No JVM, no Spark install: tasks
# send DataFrame plans to the `spark` service over gRPC (sc://spark:15002).
#
# Airflow's own protobuf version is left alone: jobs read the event schema
# from data/gen/events.desc at runtime, not from generated code, so they work
# with whatever protobuf runtime the image ships.
FROM apache/airflow:3.2.0
RUN pip install --no-cache-dir "pyspark-client==4.2.0"
