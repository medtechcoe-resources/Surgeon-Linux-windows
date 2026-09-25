import queue
import subprocess
import threading
import time
from typing import Callable, Optional

import numpy as np

from .audio_device_manager import AudioDeviceManager
from .speech_recognizer import SpeechRecognizer


class DictationWorker:
    """
    Background Whisper dictation worker.

    Audio capture and Whisper inference run in separate threads so
    microphone capture continues while Whisper is processing.
    """

    SAMPLE_RATE = 16000
    CHUNK_BYTES = 4000

    # Submit approximately every 1.5 seconds.
    TRANSCRIPTION_INTERVAL = 1.5

    def __init__(
        self,
        model_path: str,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.model_path = model_path
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_error = on_error

        self._process = None

        self._capture_thread = None
        self._inference_thread = None

        self._stop_event = threading.Event()
        self._recognizer = None

        self._audio_queue = queue.Queue()
        self._audio_buffer = bytearray()
        self._buffer_lock = threading.Lock()

    def start(self) -> bool:
        if (
            self._capture_thread
            and self._capture_thread.is_alive()
        ):
            return False

        device = AudioDeviceManager().get_default_input()

        if device is None:
            self._emit_error("NO MICROPHONE AVAILABLE")
            return False

        try:
            # Whisper model loading happens before the capture thread
            # starts, preserving the existing behavior.
            self._recognizer = SpeechRecognizer(self.model_path)

            command = [
                "pw-record",
                "--target", str(device.node_id),
                "--rate", str(self.SAMPLE_RATE),
                "--channels", "1",
                "--format", "s16",
                "-",
            ]

            self._stop_event.clear()
            self._audio_queue = queue.Queue()

            with self._buffer_lock:
                self._audio_buffer.clear()

            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="DictationAudioCapture",
            )

            self._inference_thread = threading.Thread(
                target=self._inference_loop,
                daemon=True,
                name="DictationWhisperInference",
            )

            self._capture_thread.start()
            self._inference_thread.start()

            print(
                f"DICTATION STARTED: ID={device.node_id} "
                f"NAME={device.name}"
            )

            return True

        except Exception as exc:
            self._emit_error(str(exc))
            self.stop()
            return False

    def stop(self):
        self._stop_event.set()

        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=3)

        # Give the inference thread a chance to process queued audio
        # before shutting down.
        if self._inference_thread and self._inference_thread.is_alive():
            self._audio_queue.put(None)
            self._inference_thread.join(timeout=5)

        self._process = None
        self._capture_thread = None
        self._inference_thread = None

        with self._buffer_lock:
            self._audio_buffer.clear()

        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def _capture_loop(self):
        """
        Continuously consume PipeWire audio and place chunks into
        the inference queue.
        """
        try:
            while not self._stop_event.is_set():
                data = self._process.stdout.read(self.CHUNK_BYTES)

                if not data:
                    break

                self._audio_queue.put(data)

        except Exception as exc:
            if not self._stop_event.is_set():
                self._emit_error(str(exc))

    def _inference_loop(self):
        """
        Build audio windows independently from microphone capture
        and send them to Whisper.
        """
        last_transcription = time.monotonic()

        try:
            while not self._stop_event.is_set():
                try:
                    data = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if data is None:
                    break

                with self._buffer_lock:
                    self._audio_buffer.extend(data)

                now = time.monotonic()

                if (
                    now - last_transcription
                    >= self.TRANSCRIPTION_INTERVAL
                ):
                    self._transcribe_buffer()
                    last_transcription = now

            # Process remaining audio after capture stops.
            self._drain_audio_queue()

            if self._audio_buffer:
                self._transcribe_buffer()

        except Exception as exc:
            self._emit_error(str(exc))

    def _drain_audio_queue(self):
        while True:
            try:
                data = self._audio_queue.get_nowait()
            except queue.Empty:
                break

            if data is None:
                continue

            with self._buffer_lock:
                self._audio_buffer.extend(data)

    def _transcribe_buffer(self):
        with self._buffer_lock:
            if not self._audio_buffer:
                return

            audio_bytes = bytes(self._audio_buffer)
            self._audio_buffer.clear()

        if self._recognizer is None:
            return

        try:
            audio = np.frombuffer(
                audio_bytes,
                dtype=np.int16,
            ).astype(np.float32)

            if audio.size == 0:
                return

            audio /= 32768.0

            text = self._recognizer.process_audio(
                audio,
                sample_rate=self.SAMPLE_RATE,
            )

            if text:
                self._emit_final(text)

        except Exception as exc:
            self._emit_error(str(exc))

    def _emit_partial(self, text: str):
        if self.on_partial:
            self.on_partial(text)

    def _emit_final(self, text: str):
        if self.on_final:
            self.on_final(text)

    def _emit_error(self, message: str):
        if self.on_error:
            self.on_error(message)
