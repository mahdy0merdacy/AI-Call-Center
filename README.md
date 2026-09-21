# AICallCenter

Working name. Real-time voice call handling: browser/phone audio in, STT → LLM → TTS, audio back out.

## Layout

| Path              | What it is                                                                 |
| ----------------- | -------------------------------------------------------------------------- |
| `voice-pipeline/` | Copied in from an earlier project. Reference only, not wired up yet.       |
| `spikes/`         | Throwaway experiments that answer one question each. See each spike README. |

## Spikes

- [`spikes/webrtc-audio/`](spikes/webrtc-audio/) — what browser mic audio actually looks like on the
  aiortc side: frame geometry, arrival jitter, 48k→16k resample cost, PTS behaviour, signalling latency.
