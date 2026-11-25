"""Container health check: is this orchestrator's gRPC health service SERVING?

    python -m app.healthcheck        # exit 0 = serving, 1 = not (or no answer)

For Docker's HEALTHCHECK, which has no gRPC probe of its own. Kubernetes does
(`grpc: {port: 50052}`), so a pod spec should use that instead of this.
It reports NOT_SERVING from the first moment of a SIGTERM drain, by design.
"""

from __future__ import annotations

import os
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def main() -> int:
    # The port the server listens on; 127.0.0.1 reaches it whether it binds that
    # or 0.0.0.0 (the image default).
    target = f"127.0.0.1:{os.environ.get('PORT') or '50052'}"
    try:
        with grpc.insecure_channel(target) as channel:
            reply = health_pb2_grpc.HealthStub(channel).Check(
                health_pb2.HealthCheckRequest(), timeout=3.0)
    except grpc.RpcError as exc:
        print(f"unhealthy: {exc.code().name} from {target}", file=sys.stderr)
        return 1
    if reply.status != health_pb2.HealthCheckResponse.SERVING:
        print(f"unhealthy: {health_pb2.HealthCheckResponse.ServingStatus.Name(reply.status)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
