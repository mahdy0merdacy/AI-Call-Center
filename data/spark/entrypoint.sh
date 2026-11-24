#!/usr/bin/env bash
# Start a Spark Connect server in the foreground. S3 credentials come from the
# environment (a k8s Secret later), never from the image.
set -euo pipefail
exec /opt/spark/sbin/start-connect-server.sh \
  --master "local[${SPARK_CORES:-4}]" \
  --driver-memory "${SPARK_DRIVER_MEMORY:-1400m}" \
  --conf spark.hadoop.fs.s3a.access.key="${S3_ACCESS_KEY}" \
  --conf spark.hadoop.fs.s3a.secret.key="${S3_SECRET_KEY}" \
  --conf spark.connect.grpc.binding.port=15002
