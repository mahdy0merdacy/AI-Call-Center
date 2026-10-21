from google.protobuf import descriptor_pb2 as _descriptor_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Pii(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PII_NONE: _ClassVar[Pii]
    PII_IDENTIFIER: _ClassVar[Pii]
    PII_FREE_TEXT: _ClassVar[Pii]
    PII_PERSONAL: _ClassVar[Pii]

class Service(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SERVICE_UNSPECIFIED: _ClassVar[Service]
    SERVICE_MEDIA_EDGE: _ClassVar[Service]
    SERVICE_STT: _ClassVar[Service]
    SERVICE_ORCHESTRATOR: _ClassVar[Service]
    SERVICE_MCP: _ClassVar[Service]

class EndReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    END_REASON_UNSPECIFIED: _ClassVar[EndReason]
    END_REASON_CALLER_HUNG_UP: _ClassVar[EndReason]
    END_REASON_ESCALATED: _ClassVar[EndReason]
    END_REASON_COMPLETED: _ClassVar[EndReason]
    END_REASON_ERROR: _ClassVar[EndReason]

class TurnKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TURN_KIND_UNSPECIFIED: _ClassVar[TurnKind]
    TURN_KIND_SAY: _ClassVar[TurnKind]
    TURN_KIND_READY_FOR_TOOL: _ClassVar[TurnKind]
    TURN_KIND_OPEN: _ClassVar[TurnKind]
    TURN_KIND_ESCALATE: _ClassVar[TurnKind]
PII_NONE: Pii
PII_IDENTIFIER: Pii
PII_FREE_TEXT: Pii
PII_PERSONAL: Pii
SERVICE_UNSPECIFIED: Service
SERVICE_MEDIA_EDGE: Service
SERVICE_STT: Service
SERVICE_ORCHESTRATOR: Service
SERVICE_MCP: Service
END_REASON_UNSPECIFIED: EndReason
END_REASON_CALLER_HUNG_UP: EndReason
END_REASON_ESCALATED: EndReason
END_REASON_COMPLETED: EndReason
END_REASON_ERROR: EndReason
TURN_KIND_UNSPECIFIED: TurnKind
TURN_KIND_SAY: TurnKind
TURN_KIND_READY_FOR_TOOL: TurnKind
TURN_KIND_OPEN: TurnKind
TURN_KIND_ESCALATE: TurnKind
PII_FIELD_NUMBER: _ClassVar[int]
pii: _descriptor.FieldDescriptor

class Event(_message.Message):
    __slots__ = ("event_id", "call_id", "tenant_id", "source", "seq", "occurred_at_ms", "call_started", "call_ended", "speech_started", "speech_ended", "partial_transcribed", "utterance_transcribed", "stt_session_summarized", "turn_decided", "slot_filled", "slot_rejected", "endpointing_changed", "tool_invoked", "llm_completed", "turn_completed", "barged_in", "escalated", "clause_synthesized", "first_audio_played", "error_raised")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    CALL_STARTED_FIELD_NUMBER: _ClassVar[int]
    CALL_ENDED_FIELD_NUMBER: _ClassVar[int]
    SPEECH_STARTED_FIELD_NUMBER: _ClassVar[int]
    SPEECH_ENDED_FIELD_NUMBER: _ClassVar[int]
    PARTIAL_TRANSCRIBED_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_TRANSCRIBED_FIELD_NUMBER: _ClassVar[int]
    STT_SESSION_SUMMARIZED_FIELD_NUMBER: _ClassVar[int]
    TURN_DECIDED_FIELD_NUMBER: _ClassVar[int]
    SLOT_FILLED_FIELD_NUMBER: _ClassVar[int]
    SLOT_REJECTED_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTING_CHANGED_FIELD_NUMBER: _ClassVar[int]
    TOOL_INVOKED_FIELD_NUMBER: _ClassVar[int]
    LLM_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    TURN_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    BARGED_IN_FIELD_NUMBER: _ClassVar[int]
    ESCALATED_FIELD_NUMBER: _ClassVar[int]
    CLAUSE_SYNTHESIZED_FIELD_NUMBER: _ClassVar[int]
    FIRST_AUDIO_PLAYED_FIELD_NUMBER: _ClassVar[int]
    ERROR_RAISED_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    call_id: str
    tenant_id: str
    source: Source
    seq: int
    occurred_at_ms: int
    call_started: CallStarted
    call_ended: CallEnded
    speech_started: SpeechStarted
    speech_ended: SpeechEnded
    partial_transcribed: PartialTranscribed
    utterance_transcribed: UtteranceTranscribed
    stt_session_summarized: SttSessionSummarized
    turn_decided: TurnDecided
    slot_filled: SlotFilled
    slot_rejected: SlotRejected
    endpointing_changed: EndpointingChanged
    tool_invoked: ToolInvoked
    llm_completed: LlmCompleted
    turn_completed: TurnCompleted
    barged_in: BargedIn
    escalated: Escalated
    clause_synthesized: ClauseSynthesized
    first_audio_played: FirstAudioPlayed
    error_raised: ErrorRaised
    def __init__(self, event_id: _Optional[str] = ..., call_id: _Optional[str] = ..., tenant_id: _Optional[str] = ..., source: _Optional[_Union[Source, _Mapping]] = ..., seq: _Optional[int] = ..., occurred_at_ms: _Optional[int] = ..., call_started: _Optional[_Union[CallStarted, _Mapping]] = ..., call_ended: _Optional[_Union[CallEnded, _Mapping]] = ..., speech_started: _Optional[_Union[SpeechStarted, _Mapping]] = ..., speech_ended: _Optional[_Union[SpeechEnded, _Mapping]] = ..., partial_transcribed: _Optional[_Union[PartialTranscribed, _Mapping]] = ..., utterance_transcribed: _Optional[_Union[UtteranceTranscribed, _Mapping]] = ..., stt_session_summarized: _Optional[_Union[SttSessionSummarized, _Mapping]] = ..., turn_decided: _Optional[_Union[TurnDecided, _Mapping]] = ..., slot_filled: _Optional[_Union[SlotFilled, _Mapping]] = ..., slot_rejected: _Optional[_Union[SlotRejected, _Mapping]] = ..., endpointing_changed: _Optional[_Union[EndpointingChanged, _Mapping]] = ..., tool_invoked: _Optional[_Union[ToolInvoked, _Mapping]] = ..., llm_completed: _Optional[_Union[LlmCompleted, _Mapping]] = ..., turn_completed: _Optional[_Union[TurnCompleted, _Mapping]] = ..., barged_in: _Optional[_Union[BargedIn, _Mapping]] = ..., escalated: _Optional[_Union[Escalated, _Mapping]] = ..., clause_synthesized: _Optional[_Union[ClauseSynthesized, _Mapping]] = ..., first_audio_played: _Optional[_Union[FirstAudioPlayed, _Mapping]] = ..., error_raised: _Optional[_Union[ErrorRaised, _Mapping]] = ...) -> None: ...

class Source(_message.Message):
    __slots__ = ("service", "instance", "build")
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_FIELD_NUMBER: _ClassVar[int]
    BUILD_FIELD_NUMBER: _ClassVar[int]
    service: Service
    instance: str
    build: str
    def __init__(self, service: _Optional[_Union[Service, str]] = ..., instance: _Optional[str] = ..., build: _Optional[str] = ...) -> None: ...

class CallStarted(_message.Message):
    __slots__ = ("tenant_slug", "locale", "caller_ref", "synthetic", "channel")
    TENANT_SLUG_FIELD_NUMBER: _ClassVar[int]
    LOCALE_FIELD_NUMBER: _ClassVar[int]
    CALLER_REF_FIELD_NUMBER: _ClassVar[int]
    SYNTHETIC_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    tenant_slug: str
    locale: str
    caller_ref: str
    synthetic: bool
    channel: str
    def __init__(self, tenant_slug: _Optional[str] = ..., locale: _Optional[str] = ..., caller_ref: _Optional[str] = ..., synthetic: _Optional[bool] = ..., channel: _Optional[str] = ...) -> None: ...

class CallEnded(_message.Message):
    __slots__ = ("reason", "duration_ms", "turns")
    REASON_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    TURNS_FIELD_NUMBER: _ClassVar[int]
    reason: EndReason
    duration_ms: int
    turns: int
    def __init__(self, reason: _Optional[_Union[EndReason, str]] = ..., duration_ms: _Optional[int] = ..., turns: _Optional[int] = ...) -> None: ...

class SpeechStarted(_message.Message):
    __slots__ = ("at_sample",)
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    at_sample: int
    def __init__(self, at_sample: _Optional[int] = ...) -> None: ...

class SpeechEnded(_message.Message):
    __slots__ = ("at_sample", "silence_ms")
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    SILENCE_MS_FIELD_NUMBER: _ClassVar[int]
    at_sample: int
    silence_ms: int
    def __init__(self, at_sample: _Optional[int] = ..., silence_ms: _Optional[int] = ...) -> None: ...

class PartialTranscribed(_message.Message):
    __slots__ = ("text", "infer_ms")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    INFER_MS_FIELD_NUMBER: _ClassVar[int]
    text: str
    infer_ms: int
    def __init__(self, text: _Optional[str] = ..., infer_ms: _Optional[int] = ...) -> None: ...

class UtteranceTranscribed(_message.Message):
    __slots__ = ("text", "start_sample", "end_sample", "language", "ignored")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    END_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    IGNORED_FIELD_NUMBER: _ClassVar[int]
    text: str
    start_sample: int
    end_sample: int
    language: str
    ignored: bool
    def __init__(self, text: _Optional[str] = ..., start_sample: _Optional[int] = ..., end_sample: _Optional[int] = ..., language: _Optional[str] = ..., ignored: _Optional[bool] = ...) -> None: ...

class SttSessionSummarized(_message.Message):
    __slots__ = ("chunks", "infer_p50_ms", "infer_max_ms", "audio_ms", "audio_gaps", "model", "device")
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    INFER_P50_MS_FIELD_NUMBER: _ClassVar[int]
    INFER_MAX_MS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_MS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_GAPS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    chunks: int
    infer_p50_ms: float
    infer_max_ms: float
    audio_ms: int
    audio_gaps: int
    model: str
    device: str
    def __init__(self, chunks: _Optional[int] = ..., infer_p50_ms: _Optional[float] = ..., infer_max_ms: _Optional[float] = ..., audio_ms: _Optional[int] = ..., audio_gaps: _Optional[int] = ..., model: _Optional[str] = ..., device: _Optional[str] = ...) -> None: ...

class TurnDecided(_message.Message):
    __slots__ = ("turn_index", "kind", "intent", "route_score")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    ROUTE_SCORE_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    kind: TurnKind
    intent: str
    route_score: float
    def __init__(self, turn_index: _Optional[int] = ..., kind: _Optional[_Union[TurnKind, str]] = ..., intent: _Optional[str] = ..., route_score: _Optional[float] = ...) -> None: ...

class SlotFilled(_message.Message):
    __slots__ = ("turn_index", "intent", "slot", "value", "attempt")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    intent: str
    slot: str
    value: str
    attempt: int
    def __init__(self, turn_index: _Optional[int] = ..., intent: _Optional[str] = ..., slot: _Optional[str] = ..., value: _Optional[str] = ..., attempt: _Optional[int] = ...) -> None: ...

class SlotRejected(_message.Message):
    __slots__ = ("turn_index", "intent", "slot", "attempt", "max_attempts", "utterance")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    MAX_ATTEMPTS_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    intent: str
    slot: str
    attempt: int
    max_attempts: int
    utterance: str
    def __init__(self, turn_index: _Optional[int] = ..., intent: _Optional[str] = ..., slot: _Optional[str] = ..., attempt: _Optional[int] = ..., max_attempts: _Optional[int] = ..., utterance: _Optional[str] = ...) -> None: ...

class EndpointingChanged(_message.Message):
    __slots__ = ("turn_index", "silence_ms", "slot")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    SILENCE_MS_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    silence_ms: int
    slot: str
    def __init__(self, turn_index: _Optional[int] = ..., silence_ms: _Optional[int] = ..., slot: _Optional[str] = ...) -> None: ...

class ToolInvoked(_message.Message):
    __slots__ = ("turn_index", "tool", "outcome", "latency_ms", "timed_out", "result_json")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    TIMED_OUT_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    tool: str
    outcome: str
    latency_ms: int
    timed_out: bool
    result_json: str
    def __init__(self, turn_index: _Optional[int] = ..., tool: _Optional[str] = ..., outcome: _Optional[str] = ..., latency_ms: _Optional[int] = ..., timed_out: _Optional[bool] = ..., result_json: _Optional[str] = ...) -> None: ...

class LlmCompleted(_message.Message):
    __slots__ = ("turn_index", "model", "purpose", "ttft_ms", "total_ms", "prompt_tokens", "completion_tokens", "cancelled")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    PURPOSE_FIELD_NUMBER: _ClassVar[int]
    TTFT_MS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_MS_FIELD_NUMBER: _ClassVar[int]
    PROMPT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COMPLETION_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CANCELLED_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    model: str
    purpose: str
    ttft_ms: int
    total_ms: int
    prompt_tokens: int
    completion_tokens: int
    cancelled: bool
    def __init__(self, turn_index: _Optional[int] = ..., model: _Optional[str] = ..., purpose: _Optional[str] = ..., ttft_ms: _Optional[int] = ..., total_ms: _Optional[int] = ..., prompt_tokens: _Optional[int] = ..., completion_tokens: _Optional[int] = ..., cancelled: _Optional[bool] = ...) -> None: ...

class TurnCompleted(_message.Message):
    __slots__ = ("turn_index", "total_ms", "clauses", "spoken_text")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    TOTAL_MS_FIELD_NUMBER: _ClassVar[int]
    CLAUSES_FIELD_NUMBER: _ClassVar[int]
    SPOKEN_TEXT_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    total_ms: int
    clauses: int
    spoken_text: str
    def __init__(self, turn_index: _Optional[int] = ..., total_ms: _Optional[int] = ..., clauses: _Optional[int] = ..., spoken_text: _Optional[str] = ...) -> None: ...

class BargedIn(_message.Message):
    __slots__ = ("turn_index", "at_sample")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    at_sample: int
    def __init__(self, turn_index: _Optional[int] = ..., at_sample: _Optional[int] = ...) -> None: ...

class Escalated(_message.Message):
    __slots__ = ("turn_index", "intent", "reason")
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    intent: str
    reason: str
    def __init__(self, turn_index: _Optional[int] = ..., intent: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class ClauseSynthesized(_message.Message):
    __slots__ = ("say_seq", "chars", "tts_ms", "audio_bytes")
    SAY_SEQ_FIELD_NUMBER: _ClassVar[int]
    CHARS_FIELD_NUMBER: _ClassVar[int]
    TTS_MS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_BYTES_FIELD_NUMBER: _ClassVar[int]
    say_seq: int
    chars: int
    tts_ms: int
    audio_bytes: int
    def __init__(self, say_seq: _Optional[int] = ..., chars: _Optional[int] = ..., tts_ms: _Optional[int] = ..., audio_bytes: _Optional[int] = ...) -> None: ...

class FirstAudioPlayed(_message.Message):
    __slots__ = ("final_to_first_audio_ms",)
    FINAL_TO_FIRST_AUDIO_MS_FIELD_NUMBER: _ClassVar[int]
    final_to_first_audio_ms: int
    def __init__(self, final_to_first_audio_ms: _Optional[int] = ...) -> None: ...

class ErrorRaised(_message.Message):
    __slots__ = ("component", "code", "detail")
    COMPONENT_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    component: str
    code: str
    detail: str
    def __init__(self, component: _Optional[str] = ..., code: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...
