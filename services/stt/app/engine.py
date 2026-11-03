"""One model, many calls.

The whole point of this module. `PaddedAlignAttWhisper` conflates two things:

    self.model              925 MiB of weights   <- shareable, read-only
    self.segments           per-call audio buffer
    self.tokens             per-call decoder prompt
    self.last_attend_frame  per-call attention position

Instantiating it per call duplicates the 925 MiB for nothing. Measured on an
RTX 3050: a second instance costs another 924 MiB, while the per-call state is a
few MB. So we hold ONE instance and swap the per-call attributes in and out
around each inference.

Which attributes, and why exactly these:

  * `refresh_segment()` (simul_whisper.py:118-126) resets `segments`, `tokens`
    and `last_attend_frame`. That is the authoritative list of what survives
    across `infer()` calls.
  * `kv_cache` and `dec_attns` look like state but `infer()` clears both at its
    own exit (lines 307-308), so they are per-call scratch.
  * `new_segment`, `drop_count`, `keep_count` are reset at the top of `infer()`.

Note there are TWO token lists and they are not interchangeable:

    model.tokens     list of 2-D tensors, concatenated on dim=1 — the decoder
                     prompt. `infer()` both appends to it (line 305) and
                     REASSIGNS it when the buffer scrolls (line 184).
    returned tokens  1-D (line 310 squeezes) — the newly emitted tokens, which
                     we accumulate separately to decode the transcript.

Because `infer()` reassigns `segments` and `tokens` rather than only mutating
them, both must be read back out after every call. Swapping in but not out
would silently give every call the first call's buffer.

Consequence of all this: memory is flat with respect to concurrency, and
capacity becomes compute-bound instead of memory-bound.

    1 call:  925 (weights) + ~291 (activations in flight) = ~1.2 GiB
    N calls: 925 (weights) + ~291 (activations in flight) = ~1.2 GiB
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

import torch

from .whisper_compat import REPO, LiveSegmenter, install_shims

install_shims()

from simul_whisper.transcriber.config import AlignAttConfig  # noqa: E402
from simul_whisper.transcriber.simul_whisper import (  # noqa: E402
    DEC_PAD,
    PaddedAlignAttWhisper,
)


@dataclass
class SessionState:
    """Everything one call owns. A few MB, not a few hundred."""

    language: str = "en"
    segments: list = field(default_factory=list)      # model.segments
    model_tokens: list = field(default_factory=list)  # model.tokens (2-D, dim=1)
    last_attend_frame: int = 0
    output_tokens: list = field(default_factory=list)  # returned tokens (1-D, dim=0)
    emitted_text: str = ""

    def reset(self, initial_tokens, rewind_threshold: int):
        """initial_tokens is language-specific — it carries the language token."""
        self.segments = []
        self.model_tokens = [initial_tokens]
        self.last_attend_frame = -rewind_threshold
        self.output_tokens = []
        self.emitted_text = ""


class SharedEngine:
    """Owns the weights. Serialises inference across every call on this GPU.

    The lock is not a limitation to engineer around: one GPU runs one kernel at a
    time regardless, and measurement showed batching gives no per-stream speedup
    on laptop-class hardware (143/140/142 ms per stream at batch 2/4/8). The lock
    makes the state swap safe and costs nothing real.
    """

    def __init__(self, model_path: str, cif_path: str | None, language: str,
                 segment_length: float = 0.5, frame_threshold: int = 12,
                 buffer_len: float = 20.0):
        cif = cif_path or str(REPO / "cif_models" / f"{Path(model_path).stem}.pt")
        self.cfg = AlignAttConfig(
            model_path=model_path,
            segment_length=segment_length,
            frame_threshold=frame_threshold,
            language=language,
            buffer_len=buffer_len,
            min_seg_len=0.0,
            if_ckpt_path=cif,
        )
        self.segment_length = segment_length
        self.model_name = Path(model_path).stem
        self._m = PaddedAlignAttWhisper(self.cfg)
        self._lock = asyncio.Lock()
        self.default_language = language
        # Language only affects the tokenizer and the start-of-transcript
        # sequence — the 925 MiB of weights are language-agnostic, because
        # Whisper multilingual is one model. So a second language costs a
        # tokenizer, not another model.
        self._langs: dict[str, tuple] = {}
        self._lang_pack(language)

    def _lang_pack(self, language: str) -> tuple:
        """(tokenizer, initial_tokens) for a language. Built once, then cached."""
        lang = (language or self.default_language or "en").split("-")[0].lower()
        if lang not in self._langs:
            from simul_whisper.whisper import tokenizer as wtok
            tk = wtok.get_tokenizer(multilingual=True, language=lang, task="transcribe")
            it = torch.tensor(tk.sot_sequence, dtype=torch.long,
                              device=self._m.model.device).unsqueeze(0)
            self._langs[lang] = (tk, it)
        return self._langs[lang]

    @property
    def device(self) -> str:
        return str(self._m.model.device)

    def new_state(self, language: str | None = None) -> SessionState:
        lang = (language or self.default_language).split("-")[0].lower()
        _, initial = self._lang_pack(lang)
        s = SessionState(language=lang)
        s.reset(initial, self.cfg.rewind_threshold)
        return s

    def new_segmenter(self) -> LiveSegmenter:
        return LiveSegmenter(self.segment_length)

    # -- the state swap ---------------------------------------------------

    def _infer_sync(self, state: SessionState, chunk: torch.Tensor, is_last: bool) -> str:
        """Swap this call's state in, infer, swap it back out, decode.

        Runs in a thread executor: inference takes ~200 ms while audio arrives
        every 20 ms, so running it on the event loop would stall every other call
        this worker is serving.
        """
        m = self._m
        # Swap the language in too. The StreamConfig carries it per call, and a
        # French tenant decoded with an English start-of-transcript sequence
        # transcribes badly — which reads as "Whisper can't do French" and is not.
        tok, initial = self._lang_pack(state.language)
        m.tokenizer = tok
        m.initial_tokens = initial
        m.segments = state.segments
        m.tokens = state.model_tokens
        m.last_attend_frame = state.last_attend_frame
        try:
            new_toks = m.infer(chunk, is_last)
        finally:
            # infer() REASSIGNS these when the buffer scrolls, so read them back
            # even on failure — otherwise a raised chunk leaves this call's state
            # aliasing whatever the next call swaps in.
            state.segments = m.segments
            state.model_tokens = m.tokens
            state.last_attend_frame = m.last_attend_frame

        state.output_tokens.append(new_toks)
        hyp = torch.cat(state.output_tokens, dim=0)
        hyp = hyp[hyp < DEC_PAD]
        return m.tokenizer.decode(hyp)

    async def transcribe(self, state: SessionState, chunk: torch.Tensor,
                         is_last: bool = False) -> tuple[str, str, float]:
        """Returns (full_text, delta, infer_ms)."""
        loop = asyncio.get_running_loop()
        async with self._lock:
            t0 = time.perf_counter()
            text = await loop.run_in_executor(None, self._infer_sync, state, chunk, is_last)
            dt = (time.perf_counter() - t0) * 1000
        delta = text[len(state.emitted_text):]
        state.emitted_text = text
        return text, delta, dt

    async def end_utterance(self, state: SessionState) -> str:
        """Commit the utterance and reset this call's state.

        Atomic with the endpoint decision — which is exactly why endpointing
        lives in this service. If the orchestrator decided boundaries, audio
        still in flight could be consumed under the old state before the reset
        message arrived.
        """
        async with self._lock:
            committed = state.emitted_text
            _, initial = self._lang_pack(state.language)
            state.reset(initial, self.cfg.rewind_threshold)
        return committed

    def warm_up(self) -> float:
        """Pay CUDA kernel compilation before any caller is on the line.

        Measured 630-1100 ms for the first inference. Readiness must not pass
        until this has run, or the first call to a fresh pod wears it.
        """
        t0 = time.perf_counter()
        state = self.new_state()
        seg = self.new_segmenter()
        self._infer_sync(state, torch.zeros(seg.samples_in_chunk, dtype=torch.float32), False)
        return (time.perf_counter() - t0) * 1000
