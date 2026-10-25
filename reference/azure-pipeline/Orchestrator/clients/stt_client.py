import asyncio
import websockets
from typing import AsyncGenerator


class STTClient:
    """
    Mirrors STT service exactly:
    -sends:Raw audio bytes
    - revieve: recongnized text strings as they arrive
    - no sentinel- connection statys open for the session lifetime
    """

    def __init__(self,url:str):
        self.url=url
        self._ws=None
        pass
    async def connect(self):
        self._ws=await websockets.connect(self.url)
    
    async def send_audio(self,chunk:bytes):
        print(f"[STT CLIENT] 📤 sending {len(chunk)} bytes to STT")
        
        await self._ws.send(chunk)
    
    async def recv_text(self)->str | None:
        '''
        traja3lk text ml azure maghir mateblokih, ken lkat trajaa string
        sinon trajaa none


        '''
        try:
            msg=await self._ws.recv()
            return msg
        except Exception as e:
            return None
    
    async def close(self):
        if self._ws:
            await self._ws.close()
            