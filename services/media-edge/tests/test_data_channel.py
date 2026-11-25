"""Messages sent before the browser's data channel opens are held, not dropped.

The call starts on the audio track; the data channel opens a moment later. The
orchestrator's Ready (the page's "tenant" message) and the first greeting clause
regularly land in that gap, and used to be thrown away.
"""

from __future__ import annotations

import json

import server


class FakeChannel:
    """The slice of aiortc's RTCDataChannel the edge uses."""

    def __init__(self, state: str):
        self.readyState = state
        self.sent: list = []
        self.handlers: dict = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def send(self, item):
        if self.readyState != "open":
            raise RuntimeError("channel not open")
        self.sent.append(item)

    def open(self):
        self.readyState = "open"
        self.handlers["open"]()


def _call() -> server.Call:
    return server.Call(pc=None, cfg={"session_id": "acme-1"})


def test_messages_before_the_channel_exists_are_flushed_in_order_when_it_opens():
    call = _call()
    call.send({"type": "tenant", "name": "Acme Delivery"})
    call.send_audio(b"RIFF-greeting")
    channel = FakeChannel("open")
    call.attach(channel)
    assert [json.loads(channel.sent[0])["type"], channel.sent[1]] == ["tenant", b"RIFF-greeting"]
    assert call.pending == []


def test_a_channel_still_connecting_gets_them_on_its_open_event():
    call = _call()
    channel = FakeChannel("connecting")
    call.attach(channel)
    call.send({"type": "tenant"})
    assert channel.sent == []
    channel.open()
    assert [json.loads(m)["type"] for m in channel.sent] == ["tenant"]


def test_once_open_messages_go_straight_out():
    call = _call()
    channel = FakeChannel("open")
    call.attach(channel)
    call.send({"type": "speaking", "text": "Hi"})
    assert len(channel.sent) == 1 and call.pending == []


def test_the_hold_is_bounded_and_closed_calls_hold_nothing():
    call = _call()
    for i in range(server.Call.PENDING_MAX + 10):
        call.send({"type": "partial", "i": i})
    assert len(call.pending) == server.Call.PENDING_MAX
    closed = _call()
    closed.closed = True
    closed.send({"type": "tenant"})
    assert closed.pending == []
