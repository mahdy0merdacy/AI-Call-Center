"""The agent's voice as a WebRTC track, and the filter for what Whisper hears in silence.

The voice used to reach the browser as WAV over the data channel; the browser's
echo canceller never saw it, so the agent heard (and answered) itself. These pin
the track that replaced it, and the noise filter in front of the orchestrator.
"""

from __future__ import annotations

import asyncio
import io
import wave

import numpy as np
import pytest

import server


def _wav(seconds: float, rate: int = 22050, value: int = 1000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.full(int(seconds * rate), value, np.int16).tobytes())
    return buf.getvalue()


def test_tts_wav_is_resampled_to_48k_mono_int16():
    pcm = server.wav_to_voice(_wav(1.0))
    assert pcm.dtype == np.int16
    assert abs(len(pcm) - 48000) <= 1
    assert int(pcm[1000]) == 1000                 # level preserved


def test_a_non_16_bit_wav_is_refused():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80" * 800)
    with pytest.raises(ValueError):
        server.wav_to_voice(buf.getvalue())


def test_the_track_plays_queued_speech_then_silence_in_20ms_frames():
    voice = server.AgentVoice()
    voice.say(np.full(server.VOICE_FRAME + 100, 500, np.int16))   # 1 frame + a bit
    assert voice.busy
    first, second, third = voice.next_frame(), voice.next_frame(), voice.next_frame()
    assert len(first) == len(second) == len(third) == server.VOICE_FRAME
    assert (first == 500).all()
    assert (second[:100] == 500).all() and (second[100:] == 0).all()   # tail, then padding
    assert (third == 0).all() and not voice.busy


def test_barge_in_clears_everything_not_yet_played():
    voice = server.AgentVoice()
    voice.say(np.full(48000, 700, np.int16))
    voice.next_frame()
    voice.clear()
    assert not voice.busy
    assert (voice.next_frame() == 0).all()


def test_recv_paces_frames_in_real_time():
    voice = server.AgentVoice()

    async def five():
        t0 = asyncio.get_running_loop().time()
        frames = [await voice.recv() for _ in range(5)]
        return frames, asyncio.get_running_loop().time() - t0

    frames, took = asyncio.run(five())
    assert [f.pts for f in frames] == [0, 960, 1920, 2880, 3840]
    assert frames[0].sample_rate == 48000
    assert 0.06 <= took <= 0.5                    # 4 frame periods of 20 ms, not instant


@pytest.mark.parametrize("text", [
    "Thanks for watching!", "thank you for watching.", "Thank you so much for watching",
    "Please subscribe to my channel", "Subtitles by the Amara.org community",
    "Sous-titrage ST' 501", "Merci d'avoir regardé !", "...", "[BLANK_AUDIO]", "(music)", "a",
])
def test_silence_hallucinations_are_noise(text):
    assert server.is_noise(text)


@pytest.mark.parametrize("text", [
    "Thank you.", "Thanks, that's all.", "Yes", "No.", "Bye!", "Where is my parcel?",
    "Zero one four two, eight eight seven, seven one one.", "Merci beaucoup",
])
def test_what_callers_actually_say_is_kept(text):
    assert not server.is_noise(text)
