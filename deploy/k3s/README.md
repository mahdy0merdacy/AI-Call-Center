# The core stack on k3s

Same images as `deploy/compose.yaml`, placed on three nodes:

| Label `aicc.io/role` | Node | Runs |
| --- | --- | --- |
| `voice` | fedora (control plane, RTX 3050) | edge, stt, tts, orchestrator, MCP servers, postgres, knowledge |
| `platform` | laptop-imen-wsl (no GPU) | web, portal, documents, mongo, ingestion, mailpit |
| `llm` | predator (12 GB VRAM) | the LLM, later; the data stack, later |

Why this split: a call turn runs edge → stt → orchestrator → knowledge → postgres, so that chain
stays on one node (no hops over Tailscale), on the node that is always up. If the platform node
is off, sign-ups and uploads stop but every client can still be called.

## Layers, applied in order

Each folder is one kustomization; apply one, check it, then the next.

| Folder | What | Healthy when |
| --- | --- | --- |
| `00-base` | the `aicc` namespace | `kubectl get ns aicc` |
| `10-data` | postgres, mongo, mailpit, and the `db-grants` / `mongo-setup` Jobs | both StatefulSets `1/1`, both Jobs `Complete` |
| `20-platform` | portal, documents, web, ingestion | all `1/1` on the platform node |
| `30-voice` | knowledge, MCP servers, orchestrator, stt, tts, edge | all `1/1` on fedora; `scripts/webrtc_smoke.py --k8s` passes |
| `40-data` | **namespace `aicc-data`**, on predator: Kafka, Schema Registry, Kafka UI, Postgres (data), SeaweedFS, Spark, Airflow + Jobs for topics, schemas, lake bucket | Jobs `Complete`, Airflow healthy |
| `50-sentiment` | Souha's sentiment service: a training Job on predator's GPU, then the Kafka worker | `sentiment-train` Complete, worker `1/1` |

Where things are (any node's IP works for a NodePort):

| What | URL |
| --- | --- |
| portal (sign up, documents) | http://localhost:30808 |
| voice test page | http://localhost:8082 (fedora, host network) |
| Mailpit | `kubectl -n aicc port-forward svc/mailpit 8025` → http://localhost:8025 |
| Kafka UI | http://localhost:30890 |
| Airflow | http://localhost:30895 |
| Spark UI | `kubectl -n aicc-data port-forward svc/spark 4040` |

Everything found along the way, and why each manifest looks the way it does: `FINDINGS.md`.

```bash
scripts/k3s-secrets.sh                      # Secrets from deploy/.env, keys and the root .env
kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/k3s/10-data | kubectl apply -f -
kubectl -n aicc get pods -w                  # watch them come up
```

`--load-restrictor LoadRestrictionsNone` lets a layer reuse files that live elsewhere in the repo
(`deploy/postgres/grants.sql`, `services/documents/docker/mongo-setup.js`, `tenants/`) instead of
keeping a copy that would drift.

## Reading the manifests (for learning)

- **StatefulSet vs Deployment**: a database pod keeps its name (`postgres-0`) and its volume
  across restarts. `volumeClaimTemplates` creates one PersistentVolumeClaim per pod; k3s's
  `local-path` provisioner backs it with a folder on the node, so the pod is tied to that node.
- **Headless Service** (`clusterIP: None`): the DNS name `postgres` resolves straight to the pod,
  the same name compose used, so the app configuration does not change.
- **Jobs instead of `depends_on`**: kubernetes has no start order. `db-grants` simply fails
  while postgres is not up yet and the Job retries it (`backoffLimit`) until it succeeds. Jobs
  are immutable once created, so `ttlSecondsAfterFinished` deletes a finished one and the next
  apply recreates it: they re-run on every deploy, like in compose.
- **Probes**: `readinessProbe` decides whether a pod receives traffic; `livenessProbe` decides
  whether it is restarted. A readiness check used as a liveness probe restarts pods that are
  only waiting for a dependency, so they differ here on purpose.
- **Images**: manifests say `aicc/postgres`; the `images:` block of each kustomization rewrites
  that to the registry and the git commit tag in one place.
