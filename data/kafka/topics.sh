#!/usr/bin/env bash
# Create (or re-apply config to) every topic in BIG-DATA.md §8. Safe to re-run.
#
#   data/kafka/topics.sh            # single broker: RF=1
#   RF=3 data/kafka/topics.sh       # step 3b, three brokers
#
# Runs the Kafka CLI inside kafka-1, so nothing needs installing on the host.
set -euo pipefail

RF=${RF:-1}
# With RF=3, an acked write must be on 2 replicas (DDIA.md §10). With RF=1
# there is only one, so 1 is the only legal value.
MIN_ISR=$(( RF >= 3 ? 2 : 1 ))
BOOTSTRAP=kafka-1:19092

k() { docker exec kafka-1 /opt/kafka/bin/"$@"; }

DAY=86400000
MB=1048576

# name | partitions | cleanup | retention.ms | retention.bytes (PER PARTITION)
TOPICS=(
  "calls.events.v1|6|delete|$((7 * DAY))|$((256 * MB))"
  "calls.partials.v1|6|delete|$((1 * DAY))|$((64 * MB))"
  "calls.summary.v1|6|compact|-1|-1"
  "dlq.calls.events.v1|1|delete|$((14 * DAY))|$((256 * MB))"
)

for row in "${TOPICS[@]}"; do
  IFS='|' read -r name parts cleanup ret_ms ret_bytes <<<"$row"
  configs=(
    "cleanup.policy=$cleanup"
    "retention.ms=$ret_ms"
    "retention.bytes=$ret_bytes"
    # Broker stamps arrival time, so broker_ts - occurred_at_ms = lag (DDIA.md §5).
    "message.timestamp.type=LogAppendTime"
    "min.insync.replicas=$MIN_ISR"
  )

  if k kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list | grep -qx "$name"; then
    # Partition count is deliberately NOT changed here: adding partitions remaps
    # keys and breaks per-call ordering (DDIA.md §8). That is a manual decision.
    k kafka-configs.sh --bootstrap-server "$BOOTSTRAP" --alter \
      --entity-type topics --entity-name "$name" \
      --add-config "$(IFS=,; echo "${configs[*]}")" >/dev/null
    echo "updated  $name"
  else
    args=()
    for c in "${configs[@]}"; do args+=(--config "$c"); done
    k kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --create --topic "$name" \
      --partitions "$parts" --replication-factor "$RF" "${args[@]}" >/dev/null
    echo "created  $name  (partitions=$parts rf=$RF)"
  fi
done

echo
k kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --describe --exclude-internal \
  | grep -v "^\s*Topic:.*Partition:" 
