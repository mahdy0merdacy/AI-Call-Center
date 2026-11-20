# Sentiment service

Predicts the caller's **emotion** for every utterance, from the STT text plus two orchestrator
signals, and summarises each call. The model runs locally (CPU at inference time); no external API.

- **Emotions:** neutral, satisfied, confused, frustrated, angry, anxious, sad (`app/emotions.py`).
- **Languages:** English, French, Arabic.
- **Model:** `xlm-roberta-base`, fine-tuned. Input is `[fails=N][barge] <text>`: the number of rejected
  slots so far and whether the caller interrupted are given as a text prefix.

> **Status:** trained and evaluated on **synthetic** conversations only (`scripts/synth_data.py`).
> The 1.000 test score on that data shows the pipeline works; it says nothing about real calls.
> Real quality needs real, human-labelled calls in the same schema (`app/schema.py`).

## Two ways to run it

### 1. HTTP API (`app/service.py`)

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness and the loaded model |
| `POST /predict` | One utterance: `{"text", "slot_failures", "barge_in", "top_k"}` |
| `POST /predict-call` | A whole call: `{"call_id", "turns": [...]}` → per-turn emotion + summary |

The call summary has `dominant`, `first`, `last`, `counts`, `trend` (improving / stable / worsening)
and `escalation_risk` (ended angry or frustrated, or upset on 2+ utterances). See `app/summary.py`.

```
PYTHONPATH=gen uvicorn app.service:app --port 8080     # then open /docs
```

### 2. Kafka worker (`app/consumer.py`)

Reads `calls.events.v1` (UtteranceTranscribed, SlotRejected, BargedIn, CallEnded), classifies each
utterance, and writes `EmotionClassified` and `CallEmotionSummarized` events to `calls.sentiment.v1`.
Delivery is at-least-once: offsets are committed only after the results are delivered, and repeated
input events are ignored (`app/tracker.py`).

```
PYTHONPATH=gen python -m app.consumer
PYTHONPATH=gen python scripts/e2e_check.py      # sends a fake call, prints the results
```

Environment: `KAFKA_BOOTSTRAP` (`localhost:9092`), `SCHEMA_REGISTRY` (`http://localhost:8085`),
`MODEL_DIR`, `MODEL_VERSION`, `GROUP_ID` (`sentiment-v1`).

Prerequisite: the topic and schema must exist (`data/kafka/topics.sh`, `data/kafka/register_schemas.py`).
In Git Bash on Windows, run the topics script with `MSYS_NO_PATHCONV=1`.

## Docker

The model (about 1 GB) is **not** in the image; mount it and set `MODEL_DIR` (default `/models/current`).

```
docker build -t sentiment services/sentiment
docker run -p 8080:8080 -v ./models/<version>/model:/models/current:ro sentiment                    # API
docker run --entrypoint python -v ./models/<version>/model:/models/current:ro sentiment -m app.consumer   # worker
```

## Training

Training runs outside Docker, in a local venv with a CUDA build of torch (`requirements-train.txt`).

```
python scripts/synth_data.py --calls 3000 --out data/synthetic
python scripts/train.py --data data/synthetic        # writes models/<timestamp>/model + metrics.json
python scripts/predict.py                            # interactive hand-testing
```

`train.py` builds the input prefix, tokenises and maps labels itself. Real data later needs a separate
cleaning step (empty or garbled STT, duplicates, personal data, human labels) that outputs the same
`jsonl` schema.

## The event contract

The worker uses the shared schema in `data/proto/aicallcenter/events/v1/events.proto`. `gen/` is a
committed copy of `data/gen/`; CI fails if they differ (`contract` job). After changing the proto:
run `data/gen.sh`, then copy `data/gen/aicallcenter` into `services/sentiment/gen/`.

## Known limits

- Reads text only, not tone of voice.
- Per-call state is in memory: a restart in the middle of a call forgets its slot-failure count.
- STT and the orchestrator are separate producers, so a barge-in can arrive slightly out of order with
  its utterance; the model only treats it as a hint.
- Services do not publish call events yet (branch `data/kafka-publishers`), so the worker has only been
  tested with fake events.
- The Spark bronze change that lands `calls.sentiment.v1` in the lake has not been run.
