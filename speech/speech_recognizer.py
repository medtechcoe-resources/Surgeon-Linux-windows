from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor


class SpeechRecognizer:
    """
    Whisper Small speech recognition backend.

    This class keeps the existing SpeechRecognizer role while replacing
    the previous Vosk implementation.
    """

    def __init__(self, model_path: str):
        path = Path(model_path)

        if not path.exists():
            raise FileNotFoundError(f"Whisper model not found: {path}")

        self.model_path = str(path)

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32

        self.processor = AutoProcessor.from_pretrained(self.model_path)

        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self.model_path,
            dtype=self.dtype,
        )

        self.model = self.model.to(self.device)
        self.model.eval()

        print(
            f"Whisper loaded: device={self.device}, "
            f"dtype={self.dtype}"
        )

    def create_recognizer(self, sample_rate: int = 16000):
        """
        Compatibility method.

        Whisper does not use a persistent streaming recognizer like Vosk.
        The DictationWorker will use process_audio() for accumulated audio.
        """
        return WhisperAudioBuffer(
            recognizer=self,
            sample_rate=sample_rate,
        )

    def process_audio(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> str:
        """
        Transcribe a mono floating-point audio array.
        """

        if audio is None or len(audio) == 0:
            return ""

        if sample_rate != 16000:
            raise ValueError("Whisper audio must be 16000 Hz")

        audio = np.asarray(audio, dtype=np.float32)

        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

        inputs = self.processor(
            audio,
            sampling_rate=sample_rate,
            return_tensors="pt",
            return_attention_mask=True,
        )

        input_features = inputs.input_features.to(
            self.device,
            dtype=self.dtype,
        )

        attention_mask = inputs.get("attention_mask")

        if attention_mask is not None:
            attention_mask = attention_mask.to(self.device)

        with torch.inference_mode():
            generated_ids = self.model.generate(
                input_features,
                attention_mask=attention_mask,
                language="en",
                task="transcribe",
            )

        text = self.processor.batch_decode(
            generated_ids,
            skip_special_tokens=True,
        )[0]

        return text.strip()

    def process_audio_file(
        self,
        audio_file: str,
        sample_rate: int = 16000,
    ) -> str:
        """
        Transcribe a WAV/audio file.
        """

        audio, actual_sample_rate = sf.read(
            audio_file,
            dtype="float32",
        )

        if actual_sample_rate != sample_rate:
            raise ValueError(
                f"Audio must be {sample_rate} Hz, "
                f"got {actual_sample_rate} Hz"
            )

        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

        return self.process_audio(
            audio,
            sample_rate=sample_rate,
        )


class WhisperAudioBuffer:
    """
    Small compatibility wrapper used while the existing DictationWorker
    is still being migrated from Vosk to Whisper.

    Audio is accumulated until the worker asks for a transcription.
    """

    def __init__(
        self,
        recognizer: SpeechRecognizer,
        sample_rate: int = 16000,
    ):
        self.recognizer = recognizer
        self.sample_rate = sample_rate
        self._audio = bytearray()

    def accept_audio(self, data: bytes):
        self._audio.extend(data)

    def transcribe(self) -> str:
        if not self._audio:
            return ""

        audio = np.frombuffer(
            bytes(self._audio),
            dtype=np.int16,
        ).astype(np.float32)

        audio /= 32768.0

        return self.recognizer.process_audio(
            audio,
            sample_rate=self.sample_rate,
        )

    def clear(self):
        self._audio.clear()


if __name__ == "__main__":
    model = "models/speech/whisper-small"
    audio = "/tmp/aether_whisper_small_test.wav"

    recognizer = SpeechRecognizer(model)
    text = recognizer.process_audio_file(audio)

    print("===== WHISPER RESULT =====")
    print(text if text else "NO SPEECH DETECTED")
