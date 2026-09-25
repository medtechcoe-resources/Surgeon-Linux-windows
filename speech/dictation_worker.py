import json
import subprocess
import threading
from typing import Callable, Optional

from .audio_device_manager import AudioDeviceManager
from .speech_recognizer import SpeechRecognizer

class DictationWorker:
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
        self._thread = None
        self._stop_event = threading.Event()
        self._recognizer = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False

        device = AudioDeviceManager().get_default_input()

        if device is None:
            self._emit_error("NO MICROPHONE AVAILABLE")
            return False

        try:
            recognizer = SpeechRecognizer(self.model_path)
            self._recognizer = recognizer.create_recognizer(16000)

            command = [
                "pw-record",
                "--target", str(device.node_id),
                "--rate", "16000",
                "--channels", "1",
                "--format", "s16",
                "-",
            ]

            self._stop_event.clear()

            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
            )
            self._thread.start()

            print(
                f"DICTATION STARTED: ID={device.node_id} "
                f"NAME={device.name}"
            )

            return True

        except Exception as exc:
            self._emit_error(str(exc))
            return False

    def stop(self):
        self._stop_event.set()

        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        self._process = None
        self._thread = None

    def _run(self):
        try:
            while not self._stop_event.is_set():
                data = self._process.stdout.read(4000)

                if not data:
                    break

                if self._recognizer.AcceptWaveform(data):
                    result = json.loads(self._recognizer.Result())
                    text = result.get("text", "").strip()

                    if text:
                        self._emit_final(text)
                else:
                    result = json.loads(
                        self._recognizer.PartialResult()
                    )
                    text = result.get("partial", "").strip()

                    if text:
                        self._emit_partial(text)

            if self._recognizer:
                result = json.loads(self._recognizer.FinalResult())
                text = result.get("text", "").strip()

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
