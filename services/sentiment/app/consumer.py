"""Kafka worker: calls.events.v1 -> emotion model -> calls.sentiment.v1.

    python -m app.consumer

Env (defaults suit the local docker-compose stack):
  KAFKA_BOOTSTRAP     localhost:9092
  SCHEMA_REGISTRY     http://localhost:8085
  MODEL_DIR           folder of a trained model (default: newest models/<v>/model)
  MODEL_VERSION       label written into every result (default: the model folder's parent name)
  GROUP_ID            sentiment-v1

At-least-once: offsets are committed only after the results are delivered, so a
crash re-reads a few events instead of losing them. The tracker and the output
event_ids make those repeats harmless (see tracker.py).
"""
import json
import logging
import os
import signal
import urllib.request
from pathlib import Path
from types import FrameType

from confluent_kafka import Consumer, Message, Producer

from app.predictor import EmotionPredictor
from app.tracker import CallTracker
from app.wire import NotOurFormat, frame, unframe

IN_TOPIC = "calls.events.v1"
OUT_TOPIC = "calls.sentiment.v1"
BATCH = 100
log = logging.getLogger("sentiment.consumer")


def schema_id(registry: str, topic: str) -> int:
    url = f"{registry}/subjects/{topic}-value/versions/latest"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return int(json.load(r)["id"])
    except Exception as e:
        raise SystemExit(
            f"Cannot read the schema of {topic} from {registry}: {e}\n"
            "Run data/kafka/register_schemas.py first.") from e


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
    registry = os.environ.get("SCHEMA_REGISTRY", "http://localhost:8085")

    predictor = EmotionPredictor(os.environ.get("MODEL_DIR") or None)
    version = os.environ.get("MODEL_VERSION") or Path(predictor.model_dir).parent.name

    def classify(text: str, fails: int, barge: bool) -> tuple[str, float]:
        best = predictor.predict(text, fails, barge, top_k=1)[0]
        return best["emotion"], best["score"]

    tracker = CallTracker(classify, version)
    out_schema = schema_id(registry, OUT_TOPIC)
    producer = Producer({"bootstrap.servers": bootstrap, "acks": "all",
                         "enable.idempotence": True})
    consumer = Consumer({"bootstrap.servers": bootstrap,
                         "group.id": os.environ.get("GROUP_ID", "sentiment-v1"),
                         "auto.offset.reset": "earliest", "enable.auto.commit": False})
    consumer.subscribe([IN_TOPIC])

    running = True

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("sentiment consumer up: %s -> %s, model %s", IN_TOPIC, OUT_TOPIC, version)

    def send(events) -> None:
        for e in events:
            producer.produce(OUT_TOPIC, key=e.call_id.encode(), value=frame(out_schema, e))

    while running:
        msgs: list[Message] = consumer.consume(BATCH, timeout=1.0)
        for m in msgs:
            if m.error():
                log.error("kafka error: %s", m.error())
                continue
            raw = m.value()
            if raw is None:  # tombstone: nothing to classify
                continue
            try:
                event = unframe(raw)
            except NotOurFormat as e:
                log.warning("skipping %s[%s]@%s: %s", m.topic(), m.partition(), m.offset(), e)
                continue
            send(tracker.handle(event))
        send(tracker.expire())
        if msgs:
            # Results first, offsets second: never commit what was not delivered.
            if producer.flush(30) > 0:
                raise SystemExit("could not deliver results to Kafka; not committing offsets")
            consumer.commit(asynchronous=False)
        else:
            producer.poll(0)

    log.info("shutting down")
    producer.flush(10)
    consumer.close()


if __name__ == "__main__":
    main()
