import azure.cognitiveservices.speech as speechsdk
import os


class TTSService:
    def create_synthesizer(self, audio_callback):
        speech_key = os.getenv("SPEECH_KEY")
        speech_region = os.getenv("SPEECH_REGION")

        if not speech_key or not speech_region:
            raise ValueError("Missing Azure Speech credentials")

        speech_config = speechsdk.SpeechConfig(
            subscription=speech_key,
            region=speech_region
        )

        # Choose voice (change later if you want)
        speech_config.speech_synthesis_voice_name = "en-US-JennyNeural"

        # IMPORTANT: we stream audio instead of outputting to speaker
        stream = speechsdk.audio.PushAudioOutputStream(audio_callback)
        audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)

        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config
        )

        return synthesizer
    