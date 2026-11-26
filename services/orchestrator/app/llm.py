"""Which LLM answers a turn: the one on predator, and Groq when predator is away.

predator serves the model with vLLM on its own GPU (deploy/k3s/60-llm). Its
prefix cache keeps each tenant's system prompt prefilled, so a turn only pays for
the new words. It is also one consumer GPU on one WSL machine, which can be off.
A caller must not hear silence because of that, so a turn that cannot get a first
token from predator in time is answered by Groq instead.

Fallback happens only before the first token. After that the reply is already
being spoken, and starting it again from another model would repeat it.

Two things take predator out of the turns, both shared by every call on this
orchestrator, so no turn waits out a timeout that another already paid:

  * watch(): a health check every few seconds. A node that is off is skipped
    from the next check on, and used again within seconds of coming back.
  * a turn that failed on it: predator is skipped for `cooldown_s`. This is what
    catches a server that answers its health check but not a chat request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.8-27b"


@dataclass(frozen=True)
class LlmBackend:
    """One OpenAI-compatible chat completions endpoint."""
    name: str           # for logs and events: "predator", "groq"
    url: str
    model: str
    api_key: str = ""   # empty: no Authorization header (vLLM in the cluster)

    @property
    def models_url(self) -> str:
        """GET /v1/models, the health check every OpenAI-compatible server has."""
        base = self.url.removesuffix("/").removesuffix("/chat/completions")
        return f"{base}/models"


class LlmRouter:
    """The order to try backends in, and what happens when one fails."""

    def __init__(self, primary: LlmBackend, fallback: LlmBackend | None = None, *,
                 first_token_timeout_s: float = 3.0, cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.primary = primary
        self.fallback = fallback
        self.first_token_timeout_s = first_token_timeout_s
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._down_until = 0.0     # a turn failed on the primary: skip it until then
        self.healthy = True         # the last health check passed (watch())

    def primary_down(self) -> bool:
        return not self.healthy or self._clock() < self._down_until

    def order(self) -> list[LlmBackend]:
        """Backends for this turn, in order. Predator in its cooldown is skipped,
        unless there is nothing else to try."""
        if self.fallback is None:
            return [self.primary]
        if self.primary_down():
            return [self.fallback]
        return [self.primary, self.fallback]

    def failed(self, backend: LlmBackend) -> None:
        if backend is self.primary and self.fallback is not None:
            self._down_until = self._clock() + self.cooldown_s

    def succeeded(self, backend: LlmBackend) -> None:
        if backend is self.primary:
            self._down_until = 0.0

    async def watch(self, interval_s: float = 5.0, timeout_s: float = 2.0) -> None:
        """Check the primary every interval_s, until cancelled. Only worth running
        with a fallback: without one, the primary is tried whatever it says."""
        async with aiohttp.ClientSession() as session:
            while True:
                await self.check(session, timeout_s)
                await asyncio.sleep(interval_s)

    async def check(self, session: aiohttp.ClientSession, timeout_s: float = 2.0) -> bool:
        b = self.primary
        headers = {"Authorization": f"Bearer {b.api_key}"} if b.api_key else {}
        try:
            async with session.get(b.models_url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                ok, why = resp.status == 200, f"HTTP {resp.status}"
        except (aiohttp.ClientError, TimeoutError) as exc:
            ok, why = False, type(exc).__name__
        if ok != self.healthy:
            if ok:
                log.info("LLM %s is back: turns use it again", b.name)
            else:
                fallback = self.fallback.name if self.fallback else "nothing"
                log.warning("LLM %s failed its health check (%s): turns use %s",
                            b.name, why, fallback)
        self.healthy = ok
        return ok
