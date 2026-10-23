import os
import pytest
from unittest.mock import MagicMock
from app.tts_service import TTSService

def test_missing_env(monkeypatch):
    monkeypatch.delenv("SPEECH_KEY", raising=False)
    monkeypatch.delenv("SPEECH_REGION", raising=False)

    service = TTSService()
    callback = MagicMock()

    with pytest.raises(ValueError):
        service.create_synthesizer(callback)


def test_create_synthesizer(monkeypatch):
    monkeypatch.setenv("SPEECH_KEY", "fake")
    monkeypatch.setenv("SPEECH_REGION", "fake")

    service = TTSService()
    callback = MagicMock()

    synth = service.create_synthesizer(callback)

    assert synth is not None