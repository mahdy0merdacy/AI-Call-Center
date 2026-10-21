from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CallEvent(_message.Message):
    __slots__ = ("start", "transcript", "speech_started", "speech_ended", "end")
    START_FIELD_NUMBER: _ClassVar[int]
    TRANSCRIPT_FIELD_NUMBER: _ClassVar[int]
    SPEECH_STARTED_FIELD_NUMBER: _ClassVar[int]
    SPEECH_ENDED_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    start: StartCall
    transcript: Transcript
    speech_started: SpeechStarted
    speech_ended: SpeechEnded
    end: EndCall
    def __init__(self, start: _Optional[_Union[StartCall, _Mapping]] = ..., transcript: _Optional[_Union[Transcript, _Mapping]] = ..., speech_started: _Optional[_Union[SpeechStarted, _Mapping]] = ..., speech_ended: _Optional[_Union[SpeechEnded, _Mapping]] = ..., end: _Optional[_Union[EndCall, _Mapping]] = ...) -> None: ...

class StartCall(_message.Message):
    __slots__ = ("tenant_slug", "call_id", "caller_ref")
    TENANT_SLUG_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_REF_FIELD_NUMBER: _ClassVar[int]
    tenant_slug: str
    call_id: str
    caller_ref: str
    def __init__(self, tenant_slug: _Optional[str] = ..., call_id: _Optional[str] = ..., caller_ref: _Optional[str] = ...) -> None: ...

class Transcript(_message.Message):
    __slots__ = ("text", "is_final", "at_sample")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    IS_FINAL_FIELD_NUMBER: _ClassVar[int]
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    text: str
    is_final: bool
    at_sample: int
    def __init__(self, text: _Optional[str] = ..., is_final: _Optional[bool] = ..., at_sample: _Optional[int] = ...) -> None: ...

class SpeechStarted(_message.Message):
    __slots__ = ("at_sample",)
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    at_sample: int
    def __init__(self, at_sample: _Optional[int] = ...) -> None: ...

class SpeechEnded(_message.Message):
    __slots__ = ("silence_ms",)
    SILENCE_MS_FIELD_NUMBER: _ClassVar[int]
    silence_ms: int
    def __init__(self, silence_ms: _Optional[int] = ...) -> None: ...

class EndCall(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class Directive(_message.Message):
    __slots__ = ("ready", "say", "endpointing", "cancel", "hangup", "turn")
    READY_FIELD_NUMBER: _ClassVar[int]
    SAY_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTING_FIELD_NUMBER: _ClassVar[int]
    CANCEL_FIELD_NUMBER: _ClassVar[int]
    HANGUP_FIELD_NUMBER: _ClassVar[int]
    TURN_FIELD_NUMBER: _ClassVar[int]
    ready: Ready
    say: Say
    endpointing: SetEndpointing
    cancel: Cancel
    hangup: Hangup
    turn: TurnInfo
    def __init__(self, ready: _Optional[_Union[Ready, _Mapping]] = ..., say: _Optional[_Union[Say, _Mapping]] = ..., endpointing: _Optional[_Union[SetEndpointing, _Mapping]] = ..., cancel: _Optional[_Union[Cancel, _Mapping]] = ..., hangup: _Optional[_Union[Hangup, _Mapping]] = ..., turn: _Optional[_Union[TurnInfo, _Mapping]] = ...) -> None: ...

class Ready(_message.Message):
    __slots__ = ("tenant_id", "display_name", "locale", "voice", "endpointing", "flows", "tools")
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    LOCALE_FIELD_NUMBER: _ClassVar[int]
    VOICE_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTING_FIELD_NUMBER: _ClassVar[int]
    FLOWS_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    tenant_id: str
    display_name: str
    locale: str
    voice: str
    endpointing: SetEndpointing
    flows: _containers.RepeatedScalarFieldContainer[str]
    tools: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, tenant_id: _Optional[str] = ..., display_name: _Optional[str] = ..., locale: _Optional[str] = ..., voice: _Optional[str] = ..., endpointing: _Optional[_Union[SetEndpointing, _Mapping]] = ..., flows: _Optional[_Iterable[str]] = ..., tools: _Optional[_Iterable[str]] = ...) -> None: ...

class Say(_message.Message):
    __slots__ = ("text", "seq", "end_of_turn")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    END_OF_TURN_FIELD_NUMBER: _ClassVar[int]
    text: str
    seq: int
    end_of_turn: bool
    def __init__(self, text: _Optional[str] = ..., seq: _Optional[int] = ..., end_of_turn: _Optional[bool] = ...) -> None: ...

class SetEndpointing(_message.Message):
    __slots__ = ("silence_ms", "min_speech_ms", "vad_threshold")
    SILENCE_MS_FIELD_NUMBER: _ClassVar[int]
    MIN_SPEECH_MS_FIELD_NUMBER: _ClassVar[int]
    VAD_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    silence_ms: int
    min_speech_ms: int
    vad_threshold: float
    def __init__(self, silence_ms: _Optional[int] = ..., min_speech_ms: _Optional[int] = ..., vad_threshold: _Optional[float] = ...) -> None: ...

class Cancel(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class Hangup(_message.Message):
    __slots__ = ("reason", "message")
    REASON_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    reason: str
    message: str
    def __init__(self, reason: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class TurnInfo(_message.Message):
    __slots__ = ("kind", "intent", "slots", "tool", "outcome", "llm_ms")
    class SlotsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    KIND_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    SLOTS_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    LLM_MS_FIELD_NUMBER: _ClassVar[int]
    kind: str
    intent: str
    slots: _containers.ScalarMap[str, str]
    tool: str
    outcome: str
    llm_ms: int
    def __init__(self, kind: _Optional[str] = ..., intent: _Optional[str] = ..., slots: _Optional[_Mapping[str, str]] = ..., tool: _Optional[str] = ..., outcome: _Optional[str] = ..., llm_ms: _Optional[int] = ...) -> None: ...
