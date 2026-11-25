# Airflow + the Spark Connect thin client + the data-quality runner.
# Build context is data/ (see docker-compose.yml), for quality/requirements.txt.
#
# Spark: no JVM, no Spark install. Tasks send DataFrame plans to the `spark`
# service over gRPC (sc://spark:15002). Airflow's own protobuf version is left
# alone: jobs read the event schema from data/gen/events.desc at runtime.
#
# Quality: Great Expectations in its OWN virtualenv, /opt/quality-venv. GX pulls
# in a large dependency tree; installed next to Airflow it would fight Airflow's
# pins. Tasks run it as a subprocess (BashOperator), so the two never share an
# interpreter.
FROM apache/airflow:3.2.0
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN pip install --no-cache-dir "pyspark-client==4.2.0"
COPY quality/requirements.txt /tmp/quality-requirements.txt
# The image runs as `airflow`, which cannot write /opt: create the folder as
# root, hand it over, install as airflow.
USER root
RUN mkdir /opt/quality-venv && chown airflow:0 /opt/quality-venv
USER airflow
RUN python -m venv /opt/quality-venv \
 && /opt/quality-venv/bin/pip install --no-cache-dir -r /tmp/quality-requirements.txt
