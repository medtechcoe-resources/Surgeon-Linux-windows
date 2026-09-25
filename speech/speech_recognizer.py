import json
from pathlib import Path
from vosk import Model, KaldiRecognizer

class SpeechRecognizer:
    def __init__(self, model_path: str):
        path = Path(model_path)

        if not path.exists():
            raise FileNotFoundError(f"Vosk model not found: {path}")

        self.model = Model(str(path))

    def create_recognizer(self, sample_rate: int = 16000):
        return KaldiRecognizer(self.model, sample_rate)

    def process_audio_file(self, audio_file: str, sample_rate: int = 16000) -> str:
        import wave

        with wave.open(audio_file, "rb") as audio:
            if audio.getnchannels() != 1:
                raise ValueError("Audio must be mono")

            if audio.getsampwidth() != 2:
                raise ValueError("Audio must be 16-bit PCM")

            if audio.getframerate() != sample_rate:
                raise ValueError(f"Audio must be {sample_rate} Hz")

            recognizer = self.create_recognizer(sample_rate)
            results = []

            while True:
                data = audio.readframes(4000)

                if not data:
                    break

                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip()

                    if text:
                        results.append(text)

            final_result = json.loads(recognizer.FinalResult())
            final_text = final_result.get("text", "").strip()

            if final_text:
                results.append(final_text)

        return " ".join(results)

if __name__ == "__main__":
    model = "models/speech/vosk-model-small-en-us-0.15"
    audio = "/tmp/aether_dynamic_mic_test.wav"

    recognizer = SpeechRecognizer(model)
    text = recognizer.process_audio_file(audio)

    print("===== VOSK RESULT =====")
    print(text if text else "NO SPEECH DETECTED")
