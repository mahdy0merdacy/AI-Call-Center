import asyncio
import pytest

class FakeSynth:
    def speak_text_async(self, text):
        class R:
            def get(self_inner):
                return "done"
        return R()


@pytest.mark.asyncio
async def test_streaming_flow():
    from main import AudioStreamCallback

    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    cb = AudioStreamCallback(queue, loop)

    # simulate Azure chunks
    cb.write(memoryview(b"chunk1"))
    cb.write(memoryview(b"chunk2"))

    assert await queue.get() == b"chunk1"
    assert await queue.get() == b"chunk2"