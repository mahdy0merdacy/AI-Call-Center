"""Glue for driving the vendored Simul-Whisper repo.

Owned by the STT service. It used to live in spikes/ and be reached with a
sys.path hop three directories up — which works from a checkout and does not
exist inside a container. Now it is an ordinary module of the service, and the
location of the vendored clone is configuration (SIMUL_WHISPER_DIR), mirroring
how the orchestrator takes TENANTS_DIR.

Three compatibility shims plus a live-stream segmenter. Kept here so the clone in
simul_whisper/ stays pristine and pullable — nothing in this file edits it.

The shims exist because the repo targets Python 3.9 / PyTorch 2.0 and we run it on
3.12 / torch 2.14:

  1. torchaudio.load routes through torchcodec since 2.9 -> fall back to soundfile
  2. the shipped CIF checkpoints were saved on CUDA -> torch.load needs map_location
     on a CPU-only box
  3. whisper's Triton median-filter kernel rewrites JITFunction.src, which Triton 3
     forbids -> use the pure-torch path that timing.py already contains
"""

import os
import sys
from pathlib import Path

import numpy as np
import torch

# Where the vendored clone lives. Defaults to the repo layout for a dev
# checkout; the image sets it to the path it copied the clone to.
REPO = Path(
    os.environ.get("SIMUL_WHISPER_DIR")
    or Path(__file__).resolve().parents[3] / "simul_whisper"
)
if not (REPO / "simul_whisper").is_dir():
    raise RuntimeError(
        f"Simul-Whisper clone not found at {REPO}. "
        "Set SIMUL_WHISPER_DIR to the directory containing simul_whisper/ and cif_models/."
    )
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _shim_torchaudio_load():
    import torchaudio

    original = torchaudio.load

    def load(path, normalize=True, **kw):
        try:
            return original(path, normalize=normalize, **kw)
        except Exception:
            import soundfile as sf

            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            return torch.from_numpy(data.T.copy()), sr

    torchaudio.load = load


def _shim_torch_load():
    """Only needed without CUDA — the checkpoints were saved on a CUDA device."""
    if torch.cuda.is_available():
        return

    original = torch.load

    def load(*a, **kw):
        kw.setdefault("map_location", "cpu")
        return original(*a, **kw)

    torch.load = load


def _shim_median_filter():
    """timing.py only catches RuntimeError, but Triton 3 raises TypeError, so its
    own fallback never fires. Bind the pure-torch implementation directly."""
    import torch.nn.functional as F
    from simul_whisper.transcriber import simul_whisper as sw

    def median_filter(x, filter_width):
        pad_width = filter_width // 2
        if x.shape[-1] <= pad_width:
            return x
        ndim = x.ndim
        if ndim <= 2:
            x = x[None, None, :]
        x = F.pad(x, (pad_width, pad_width, 0, 0), mode="reflect")
        result = x.unfold(-1, filter_width, 1).sort()[0][..., pad_width]
        if ndim <= 2:
            result = result[0, 0]
        return result

    sw.median_filter = median_filter


def install_shims():
    """Apply all three. Safe to call once, before building a model."""
    _shim_torchaudio_load()
    _shim_torch_load()
    _shim_median_filter()  # imports the transcriber, so must come after path setup


class LiveSegmenter:
    """Chunk a live 16 kHz mono stream exactly the way SegmentWrapper chunks a file.

    Simul-Whisper carries the last 240 samples of each chunk into the next one so
    the CNN sees the same context at chunk boundaries that it would offline. Get
    this wrong and streaming quality quietly degrades versus the paper's numbers.

        chunk 0 : 16240 fresh samples
        chunk n : 240 tail of chunk n-1  +  16000 fresh samples
    """

    def __init__(self, segment_length=1.0):
        from simul_whisper.whisper.audio import HOP_LENGTH, N_FFT, SAMPLE_RATE

        frames_to_read = int((segment_length * SAMPLE_RATE) / HOP_LENGTH)
        self.samples_to_read = frames_to_read * HOP_LENGTH            # 16000 @ 1.0s
        self.samples_in_chunk = self.samples_to_read + N_FFT - HOP_LENGTH  # 16240
        self.buffer_len = self.samples_in_chunk - self.samples_to_read     # 240
        self.sample_rate = SAMPLE_RATE

        self._pending = np.zeros(0, dtype=np.float32)
        self._tail = None

    @property
    def pending_seconds(self):
        return len(self._pending) / self.sample_rate

    def feed(self, samples: np.ndarray):
        """Push float32 mono samples; yield every complete chunk as a torch tensor."""
        self._pending = np.concatenate([self._pending, samples])
        while True:
            need = self.samples_in_chunk if self._tail is None else self.samples_to_read
            if len(self._pending) < need:
                return
            fresh, self._pending = self._pending[:need], self._pending[need:]
            chunk = fresh if self._tail is None else np.concatenate([self._tail, fresh])
            self._tail = chunk[-self.buffer_len:].copy()
            yield torch.from_numpy(chunk.copy())

    def flush(self):
        """Whatever is left, padded up to one chunk — for the final is_last call."""
        if len(self._pending) == 0:
            return None
        chunk = self._pending if self._tail is None else np.concatenate([self._tail, self._pending])
        self._pending = np.zeros(0, dtype=np.float32)
        return torch.from_numpy(chunk.copy())

    def reset(self):
        self._pending = np.zeros(0, dtype=np.float32)
        self._tail = None
