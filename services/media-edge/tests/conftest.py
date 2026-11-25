from __future__ import annotations

import pytest

CONFIG_ENV = ("HOST", "PORT", "STT_ADDR", "ORCHESTRATOR_ADDR", "TTS_URL", "DEFAULT_TENANT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """The flags default to these variables, and the shell running pytest may
    export some (HOST, PORT): every test starts from none of them."""
    for name in CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
