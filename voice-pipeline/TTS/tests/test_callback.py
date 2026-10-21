import asyncio
import pytest

class FakeLoop:
    def time(self):
        return 1234


def test_audio_callback_puts_chunks():
    from main import AudioStreamCallback

    loop = FakeLoop()
    queue = asyncio.Queue()

    cb = AudioStreamCallback(queue, loop)

    data = b"hello_audio"

    cb.write(memoryview(data))

    assert not queue.empty()
    chunk = asyncio.run(queue.get())

    assert chunk == data