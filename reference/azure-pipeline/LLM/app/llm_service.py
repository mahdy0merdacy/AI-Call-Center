from groq import Groq
import os

class LLMService:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("Missing GROQ_API_KEY")

        self.client = Groq(api_key=api_key)

    def stream_completion(self, prompt: str):
        completion = self.client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            stream=True,
        )

        for chunk in completion:
            token =chunk.choices[0].delta.content
            if token:
                yield token 