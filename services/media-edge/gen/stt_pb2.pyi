from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TranscribeRequest(_message.Message):
    __slots__ = ("config", "audio", "end_utterance")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    END_UTTERANCE_FIELD_NUMBER: _ClassVar[int]
    config: StreamConfig
    audio: AudioChunk
    end_utterance: EndUtterance
    def __init__(self, config: _Optional[_Union[StreamConfig, _Mapping]] = ..., audio: _Optional[_Union[AudioChunk, _Mapping]] = ..., end_utterance: _Optional[_Union[EndUtterance, _Mapping]] = ...) -> None: ...

class StreamConfig(_message.Message):
    __slots__ = ("session_id", "language", "silence_ms_to_endpoint", "min_speech_ms", "vad_threshold", "format")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    SILENCE_MS_TO_ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    MIN_SPEECH_MS_FIELD_NUMBER: _ClassVar[int]
    VAD_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    language: str
    silence_ms_to_endpoint: int
    min_speech_ms: int
    vad_threshold: float
    format: AudioFormat
    def __init__(self, session_id: _Optional[str] = ..., language: _Optional[str] = ..., silence_ms_to_endpoint: _Optional[int] = ..., min_speech_ms: _Optional[int] = ..., vad_threshold: _Optional[float] = ..., format: _Optional[_Union[AudioFormat, _Mapping]] = ...) -> None: ...

class AudioFormat(_message.Message):
    __slots__ = ("sample_rate", "channels", "encoding")
    class Encoding(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        ENCODING_UNSPECIFIED: _ClassVar[AudioFormat.Encoding]
        PCM_S16LE: _ClassVar[AudioFormat.Encoding]
    ENCODING_UNSPECIFIED: AudioFormat.Encoding
    PCM_S16LE: AudioFormat.Encoding
    SAMPLE_RATE_FIELD_NUMBER: _ClassVar[int]
    CHANNELS_FIELD_NUMBER: _ClassVar[int]
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    sample_rate: int
    channels: int
    encoding: AudioFormat.Encoding
    def __init__(self, sample_rate: _Optional[int] = ..., channels: _Optional[int] = ..., encoding: _Optional[_Union[AudioFormat.Encoding, str]] = ...) -> None: ...

class AudioChunk(_message.Message):
    __slots__ = ("pcm", "start_sample", "sent_unix_ns")
    PCM_FIELD_NUMBER: _ClassVar[int]
    START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    SENT_UNIX_NS_FIELD_NUMBER: _ClassVar[int]
    pcm: bytes
    start_sample: int
    sent_unix_ns: int
    def __init__(self, pcm: _Optional[bytes] = ..., start_sample: _Optional[int] = ..., sent_unix_ns: _Optional[int] = ...) -> None: ...

class EndUtterance(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class TranscribeEvent(_message.Message):
    __slots__ = ("ready", "speech_start", "partial", "speech_end", "final", "metrics")
    READY_FIELD_NUMBER: _ClassVar[int]
    SPEECH_START_FIELD_NUMBER: _ClassVar[int]
    PARTIAL_FIELD_NUMBER: _ClassVar[int]
    SPEECH_END_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    ready: Ready
    speech_start: SpeechStart
    partial: Partial
    speech_end: SpeechEnd
    final: Final
    metrics: Metrics
    def __init__(self, ready: _Optional[_Union[Ready, _Mapping]] = ..., speech_start: _Optional[_Union[SpeechStart, _Mapping]] = ..., partial: _Optional[_Union[Partial, _Mapping]] = ..., speech_end: _Optional[_Union[SpeechEnd, _Mapping]] = ..., final: _Optional[_Union[Final, _Mapping]] = ..., metrics: _Optional[_Union[Metrics, _Mapping]] = ...) -> None: ...

class Ready(_message.Message):
    __slots__ = ("model", "device", "segment_length_s")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_LENGTH_S_FIELD_NUMBER: _ClassVar[int]
    model: str
    device: str
    segment_length_s: float
    def __init__(self, model: _Optional[str] = ..., device: _Optional[str] = ..., segment_length_s: _Optional[float] = ...) -> None: ...

class SpeechStart(_message.Message):
    __slots__ = ("at_sample",)
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    at_sample: int
    def __init__(self, at_sample: _Optional[int] = ...) -> None: ...

class Partial(_message.Message):
    __slots__ = ("text", "delta")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    text: str
    delta: str
    def __init__(self, text: _Optional[str] = ..., delta: _Optional[str] = ...) -> None: ...

class SpeechEnd(_message.Message):
    __slots__ = ("at_sample", "silence_ms")
    AT_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    SILENCE_MS_FIELD_NUMBER: _ClassVar[int]
    at_sample: int
    silence_ms: int
    def __init__(self, at_sample: _Optional[int] = ..., silence_ms: _Optional[int] = ...) -> None: ...

class Final(_message.Message):
    __slots__ = ("text", "start_sample", "end_sample")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    END_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    text: str
    start_sample: int
    end_sample: int
    def __init__(self, text: _Optional[str] = ..., start_sample: _Optional[int] = ..., end_sample: _Optional[int] = ...) -> None: ...

class Metrics(_message.Message):
    __slots__ = ("infer_ms", "backlog_chunks", "active_sessions", "audio_s")
    INFER_MS_FIELD_NUMBER: _ClassVar[int]
    BACKLOG_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_S_FIELD_NUMBER: _ClassVar[int]
    infer_ms: int
    backlog_chunks: int
    active_sessions: int
    audio_s: float
    def __init__(self, infer_ms: _Optional[int] = ..., backlog_chunks: _Optional[int] = ..., active_sessions: _Optional[int] = ..., audio_s: _Optional[float] = ...) -> None: ...
