import pytest
from fastapi.testclient import TestClient

from app.service import create_app


class FakePredictor:
    """Says 'angry' if the text shouts or the caller barged in, else 'neutral'."""

    model_dir = "fake-model"

    def predict(self, text, slot_failures=0, barge_in=False, top_k=3):
        emotion = "angry" if "!" in text or barge_in else "neutral"
        other = "satisfied" if emotion == "neutral" else "frustrated"
        return [{"emotion": emotion, "score": 0.9}, {"emotion": other, "score": 0.1}][:top_k]


@pytest.fixture()
def client():
    with TestClient(create_app(FakePredictor())) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model": "fake-model"}


def test_predict(client):
    r = client.post("/predict", json={"text": "hello", "top_k": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["emotion"] == "neutral"
    assert len(body["top"]) == 2


def test_predict_passes_signals(client):
    r = client.post("/predict", json={"text": "hello", "barge_in": True})
    assert r.json()["emotion"] == "angry"


@pytest.mark.parametrize("payload", [
    {"text": ""},
    {"text": "x" * 2001},
    {"text": "hi", "slot_failures": -1},
    {"text": "hi", "top_k": 0},
    {},
])
def test_predict_rejects_bad_input(client, payload):
    assert client.post("/predict", json=payload).status_code == 422


def test_predict_call(client):
    turns = [{"text": "hello"}, {"text": "this is wrong!"}, {"text": "fix it!"}]
    r = client.post("/predict-call", json={"call_id": "c1", "turns": turns})
    assert r.status_code == 200
    body = r.json()
    assert body["call_id"] == "c1"
    assert [t["emotion"] for t in body["turns"]] == ["neutral", "angry", "angry"]
    assert [t["turn_idx"] for t in body["turns"]] == [0, 1, 2]
    assert body["summary"]["escalation_risk"] is True
    assert body["summary"]["trend"] == "worsening"


def test_predict_call_needs_turns(client):
    assert client.post("/predict-call", json={"turns": []}).status_code == 422
