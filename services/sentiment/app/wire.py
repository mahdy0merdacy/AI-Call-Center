"""Confluent wire format for the Event protobuf (same framing as data/kafka/smoke.py).

    0x00            magic byte
    <4 bytes>       schema id, big-endian (which registered schema wrote this)
    0x00            message index [0] = the first message in the .proto = Event
    <protobuf>      the Event
"""
import struct

from aicallcenter.events.v1 import events_pb2 as ev

HEADER_LEN = 6


class NotOurFormat(ValueError):
    """The record is not a Confluent-framed Event."""


def frame(schema_id: int, event: ev.Event) -> bytes:
    return b"\x00" + struct.pack(">I", schema_id) + b"\x00" + event.SerializeToString()


def unframe(value: bytes) -> ev.Event:
    if len(value) < HEADER_LEN or value[0] != 0 or value[5] != 0:
        raise NotOurFormat("not Confluent wire format with message index [0]")
    try:
        return ev.Event.FromString(value[HEADER_LEN:])
    except Exception as e:  # protobuf raises DecodeError, which is not worth importing
        raise NotOurFormat(f"does not decode as Event: {e}") from e
