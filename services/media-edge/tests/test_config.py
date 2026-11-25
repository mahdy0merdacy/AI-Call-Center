"""Configuration: flags for a terminal, environment variables for containers."""

from __future__ import annotations

import server

CONTAINER_ENV = {
    "HOST": "0.0.0.0",
    "PORT": "9000",
    "STT_ADDR": "stt:50051",
    "ORCHESTRATOR_ADDR": "orchestrator:50052",
    "TTS_URL": "http://tts:8083",
    "DEFAULT_TENANT": "swiftnet",
}


def _settings(args) -> tuple:
    return (args.host, args.port, args.stt, args.orchestrator, args.tts, args.default_tenant)


def test_without_env_the_defaults_are_the_single_machine_ports():
    assert _settings(server.parse_args([])) == (
        "127.0.0.1", 8082, "127.0.0.1:50051", "127.0.0.1:50052",
        "http://127.0.0.1:8083", "acme")


def test_flags_default_to_the_environment(monkeypatch):
    for name, value in CONTAINER_ENV.items():
        monkeypatch.setenv(name, value)
    assert _settings(server.parse_args([])) == (
        "0.0.0.0", 9000, "stt:50051", "orchestrator:50052", "http://tts:8083", "swiftnet")


def test_an_explicit_flag_still_wins(monkeypatch):
    for name, value in CONTAINER_ENV.items():
        monkeypatch.setenv(name, value)
    args = server.parse_args(["--port", "8082", "--orchestrator", "127.0.0.1:50052"])
    assert (args.port, args.orchestrator, args.stt) == (8082, "127.0.0.1:50052", "stt:50051")


def test_an_empty_variable_counts_as_unset(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_ADDR", "")
    monkeypatch.setenv("PORT", "")
    args = server.parse_args([])
    assert (args.orchestrator, args.port) == ("127.0.0.1:50052", 8082)
