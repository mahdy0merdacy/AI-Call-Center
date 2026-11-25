"""Turn chunk texts into vectors with BAAI/bge-m3 (1024 numbers per chunk).

The model is loaded ONCE when the worker starts (~2.3 GB download the first time,
kept in the hf_cache volume; ~3 GB of RAM while running), then reused for every
document.

RULE: the orchestrator must embed the caller's question with the SAME model and
the same normalization, or distances between question and chunks mean nothing.
That is why every row stores embedding_model.
"""

import logging
from typing import Protocol

import numpy as np

log = logging.getLogger('ingestion')

MODEL_NAME = 'BAAI/bge-m3'
# Must match the column type in 02-pgvector.sql: embedding vector(1024).
DIMENSIONS = 1024
# Our chunks are ~800 characters (~250 tokens). bge-m3 accepts 8192 tokens, but
# reading at most 512 saves memory and time and gives the same vectors for chunks
# of this size. A longer chunk is cut at 512 tokens (its start is still embedded).
MAX_TOKENS = 512
# Chunks embedded together in one pass. Bigger = a bit faster, more RAM.
BATCH_SIZE = 16


class TextEmbedder(Protocol):
    """What the worker and the knowledge API need from a model.

    Embedder (the model in this process) and remote_embedding.RemoteEmbedder (the
    model in the knowledge service) both provide it.
    """

    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class Embedder:
    """Loads the model once; embed() is then called for each document."""

    def __init__(self, model_name: str = MODEL_NAME, threads: int = 2) -> None:
        # Imported here, not at the top of the file: torch is heavy, and the unit
        # tests (cleaning, ids, storage) must run without loading it.
        import torch
        from sentence_transformers import SentenceTransformer

        # Limit CPU threads: embedding uses every core it can get, and live calls
        # (orchestrator, STT, TTS) may run on the same machine. Ingestion can be a
        # little slower; a caller must never wait because a PDF is being processed.
        if threads > 0:
            torch.set_num_threads(threads)

        log.info('loading embedding model %s (first start downloads ~2.3 GB)...', model_name)
        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device='cpu')
        self._model.max_seq_length = MAX_TOKENS

        # Warm-up + safety check: one real embedding. It makes the first document
        # as fast as the next ones, and stops the worker at startup if the model
        # does not give exactly 1024 numbers (it could not be stored in Postgres).
        dims = self.embed(['warm-up']).shape[1]
        if dims != DIMENSIONS:
            raise RuntimeError(
                f'{model_name} gives {dims} dimensions, but document_chunks.embedding '
                f'is vector({DIMENSIONS})'
            )
        log.info('embedding model ready (%d dimensions, %d CPU threads)', dims, threads)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an array of shape (len(texts), 1024), float32, one row per text.

        normalize_embeddings=True makes every vector length 1. Then cosine
        distance (the <=> operator of our hnsw index, vector_cosine_ops) compares
        only the DIRECTION of vectors, i.e. the meaning, not the text length.
        """
        vectors = self._model.encode(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
