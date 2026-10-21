# Spark 4.2 + the connectors this project uses, baked in.
#
# Baked rather than `--packages` at runtime: a pod on k3s should not need
# Maven Central to start, and every start resolving 300 MB of jars is slow.
FROM apache/spark:4.2.0-python3

USER root

# Versions must move together: Delta 4.4.x is built against Spark 4.2.0, and
# hadoop-aws must equal the Hadoop that Spark 4.2.0 ships (3.5.0).
ARG PACKAGES="io.delta:delta-spark_2.13:4.4.1,org.apache.spark:spark-sql-kafka-0-10_2.13:4.2.0,org.apache.spark:spark-protobuf_2.13:4.2.0,org.apache.hadoop:hadoop-aws:3.5.0"

# Let Spark's own resolver (Ivy) fetch the transitive closure, into a BuildKit
# cache so editing this file does not re-download 600 MB. Then copy onto the
# classpath only what Spark does not already ship. Ivy names files
# <group>_<artifact>-<version>.jar; Spark names them <artifact>-<version>.jar.
# Compare by ARTIFACT, ignoring version: two versions of one library on a JVM
# classpath means whichever sorts first wins (e.g. an old Jackson).
RUN --mount=type=cache,target=/tmp/ivy \
    printf 'from pyspark.sql import SparkSession\nSparkSession.builder.master("local[1]").getOrCreate().stop()\n' > /tmp/resolve.py \
 && /opt/spark/bin/spark-submit --conf spark.jars.ivy=/tmp/ivy --packages "$PACKAGES" /tmp/resolve.py \
 && for j in /tmp/ivy/jars/*.jar; do \
      a=$(basename "$j"); a=${a#*_}; artifact=${a%%-[0-9]*}; \
      if ls /opt/spark/jars/"$artifact"-[0-9]*.jar >/dev/null 2>&1; then \
        echo "skip $a (Spark ships $artifact)"; \
      else \
        cp "$j" /opt/spark/jars/"$a"; \
      fi; \
    done \
 && rm /tmp/resolve.py

COPY spark-defaults.conf /opt/spark/conf/spark-defaults.conf
COPY entrypoint.sh /opt/aicc-entrypoint.sh
RUN chmod +x /opt/aicc-entrypoint.sh && mkdir -p /opt/spark/work-dir && chown -R spark /opt/spark/work-dir

USER spark
ENTRYPOINT ["/opt/aicc-entrypoint.sh"]
