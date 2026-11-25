"""HTTP service around the emotion model.

Run:  uvicorn app.service:app --port 8080
Env:  MODEL_DIR = folder of a trained model (default: newest models/<version>/model)
"""
import os
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.summary import summarize

MAX_TURNS_PER_CALL = 500


class Predictor(Protocol):
    """What the service needs from a model. EmotionPredictor implements it;
    tests use a tiny fake so they do not need torch or model files."""

    def predict(self, text: str, slot_failures: int = 0, barge_in: bool = False,
                top_k: int = 3) -> list[dict]: ...


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    slot_failures: int = Field(default=0, ge=0)
    barge_in: bool = False


class PredictIn(TurnIn):
    top_k: int = Field(default=3, ge=1, le=7)


class Score(BaseModel):
    emotion: str
    score: float


class PredictOut(BaseModel):
    emotion: str
    score: float
    top: list[Score]


class CallIn(BaseModel):
    call_id: str | None = None
    turns: list[TurnIn] = Field(min_length=1, max_length=MAX_TURNS_PER_CALL)


class TurnOut(BaseModel):
    turn_idx: int
    emotion: str
    score: float


class CallSummary(BaseModel):
    turns: int
    dominant: str | None
    first: str | None
    last: str | None
    counts: dict[str, int]
    trend: str
    escalation_risk: bool


class CallOut(BaseModel):
    call_id: str | None
    turns: list[TurnOut]
    summary: CallSummary


def _load_default() -> Predictor:
    # Imported here so tests (and `--help`) never pay for torch.
    from app.predictor import EmotionPredictor

    return EmotionPredictor(os.environ.get("MODEL_DIR") or None)


def create_app(predictor: Predictor | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.predictor = predictor or _load_default()
        yield

    app = FastAPI(title="sentiment", version="0.1.0", lifespan=lifespan)

    # Plain `def` endpoints: FastAPI runs them in a thread pool, so the CPU-heavy
    # model call does not freeze the event loop.
    @app.get("/health")
    def health() -> dict:
        model = getattr(app.state, "predictor", None)
        if model is None:
            raise HTTPException(503, "model not loaded")
        return {"status": "ok", "model": str(getattr(model, "model_dir", "unknown"))}

    @app.post("/predict", response_model=PredictOut)
    def predict(body: PredictIn) -> PredictOut:
        top = app.state.predictor.predict(body.text, body.slot_failures, body.barge_in, body.top_k)
        return PredictOut(emotion=top[0]["emotion"], score=top[0]["score"], top=top)

    @app.post("/predict-call", response_model=CallOut)
    def predict_call(body: CallIn) -> CallOut:
        turns = []
        for i, t in enumerate(body.turns):
            best = app.state.predictor.predict(t.text, t.slot_failures, t.barge_in, top_k=1)[0]
            turns.append(TurnOut(turn_idx=i, emotion=best["emotion"], score=best["score"]))
        summary = CallSummary(**summarize([t.emotion for t in turns]))
        return CallOut(call_id=body.call_id, turns=turns, summary=summary)

    return app


app = create_app()
