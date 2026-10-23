import websockets
from typing import AsyncGenerator

class LLMClient:
    """
    interface ll LLM service:
    -yabaath: string (transcript mtaa LSTT)
    -yrajaa:token string (reponse) lli tousel "<END>" 
    """
    def __init__(self, url:str):
        self.url = url
        self._ws=None
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt with identity, capabilities, mission, and guardrails."""
        return (
            "You are companionIO, an intelligent voice assistant designed to help users with daily tasks, conversations, and information.\n\n"
            "Your capabilities:\n"
            "- Process voice input through speech-to-text\n"
            "- Generate natural language responses\n"
            "- Convert responses to speech for output\n"
            "- Assist with general knowledge, planning, and communication\n\n"
            "Your mission:\n"
            "To be a helpful, reliable companion that enhances users' daily lives by providing accurate information, engaging conversation, and practical assistance while maintaining a friendly and professional demeanor.\n\n"
            "Guardrails:\n"
            "- Never provide advice that could cause harm, injury, or illegal activities\n"
            "- Do not assist with creating or distributing harmful content, including violence, hate speech, or misinformation\n"
            "- Always prioritize user safety and ethical guidelines\n"
            "- If asked about restricted topics, politely decline and redirect to positive alternatives\n"
            "- Maintain privacy and do not share personal information without consent\n\n"
            "Respond naturally and helpfully to user queries.\n\n"
        )
    
    async def connect(self):
        self._ws=await websockets.connect(self.url)
    
    async def generate(self,prompt:str)->AsyncGenerator[str,None]:
        # Prepend system prompt to user prompt
        full_prompt = self._build_system_prompt() + "User: " + prompt + "\nAssistant:"
        await self._ws.send(full_prompt)


        async for token in self._ws:
            if token =="<END>":
                break
            yield token
        
    async def close(self):
        if self._ws:
            await self._ws.close()
            
