import azure.cognitiveservices.speech as speechsdk
import os 

class SpeechService:

    def create_recognizer(self):
        speech_key = os.getenv("SPEECH_KEY")
        speech_region = os.getenv("SPEECH_REGION")
        
        if not speech_key or not speech_region:
            raise ValueError("l key mafamesh aysh khouya")

        speech_config = speechsdk.SpeechConfig(
            subscription=speech_key,
            region=speech_region
        )
        stream = speechsdk.audio.PushAudioInputStream()

        audio_config = speechsdk.audio.AudioConfig(stream=stream)

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config
        )

        return recognizer, stream