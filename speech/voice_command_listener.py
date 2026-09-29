import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from vosk import Model, KaldiRecognizer


class VoiceCommandListener:
    """
    Lightweight offline voice-command listener.

    Recognizes:
        Start Aether
        Pause Aether
        Resume Aether
        Stop Aether

    Uses Vosk on CPU.
    """

    COMMANDS = {
        "start": "start",
        "pause": "pause",
        "resume": "resume",
        "stop": "stop",
    }

    def __init__(
        self,
        model_path,
        on_command=None,
        on_error=None,
        sample_rate=16000,
    ):
        self.model_path = Path(model_path)
        self.on_command = on_command
        self.on_error = on_error
        self.sample_rate = sample_rate

        self._model = None
        self._recognizer = None
        self._process = None
        self._thread = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_command = None
        self._last_command_time = 0.0

    @property
    def is_running(self):
        return self._running

    def start(self):
        if self._running:
            return False

        try:
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"Vosk model not found: {self.model_path}"
                )

            if self._model is None:
                self._model = Model(str(self.model_path))

            grammar = json.dumps([
                "start",
                "aether",
                "pause",
                "resume",
                "stop",
                "start aether",
                "pause aether",
                "resume aether",
                "stop aether",
                "[unk]",
            ])

            self._recognizer = KaldiRecognizer(
                self._model,
                self.sample_rate,
                grammar,
            )

            self._recognizer.SetWords(False)

            self._stop_event.clear()

            self._process = subprocess.Popen(
                [
                    "pw-record",
                    "--rate",
                    str(self.sample_rate),
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            self._running = True

            self._thread = threading.Thread(
                target=self._listen_loop,
                name="AetherVoiceCommandListener",
                daemon=True,
            )
            self._thread.start()

            return True

        except Exception as exc:
            self._running = False
            self._cleanup_process()
            self._emit_error(str(exc))
            return False

    def stop(self):
        self._stop_event.set()
        self._running = False
        self._cleanup_process()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        self._thread = None

    def _listen_loop(self):
        try:
            while not self._stop_event.is_set():
                if self._process is None:
                    break

                data = self._process.stdout.read(3200)

                if not data:
                    break

                if self._recognizer.AcceptWaveform(data):
                    result = self._recognizer.Result()

                    try:
                        payload = json.loads(result)
                    except json.JSONDecodeError:
                        continue

                    text = payload.get("text", "").strip()

                    if text:
                        self._process_text(text)

        except Exception as exc:
            if not self._stop_event.is_set():
                self._emit_error(str(exc))

        finally:
            self._running = False

    def _process_text(self, text):
        normalized = self._normalize(text)

        if not normalized:
            return

        command = self._detect_command(normalized)

        if not command:
            return

        # Prevent the same spoken command from firing twice
        # when Vosk finalizes overlapping/adjacent audio.
        now = time.monotonic()

        if (
            command == self._last_command
            and now - self._last_command_time < 1.5
        ):
            return

        self._last_command = command
        self._last_command_time = now

        if self.on_command:
            self.on_command(command)

    @staticmethod
    def _normalize(text):
        text = text.lower().strip()

        text = re.sub(
            r"[^a-z0-9\s]",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text

    def _detect_command(self, text):
        """
        Vosk may hear:
            "start aether"
            "start at that"
            "start ether"
            "start"

        We therefore use the command keyword rather than requiring
        an exact phrase match.
        """

        words = text.split()

        if not words:
            return None

        # Prefer the more specific multi-word commands first.
        if "resume" in words:
            return self.COMMANDS["resume"]

        if "pause" in words or "pas" in words:
            return self.COMMANDS["pause"]

        if "stop" in words:
            return self.COMMANDS["stop"]

        if "start" in words:
            return self.COMMANDS["start"]

        return None

    def _cleanup_process(self):
        process = self._process
        self._process = None

        if process is None:
            return

        try:
            if process.poll() is None:
                process.terminate()

                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except Exception:
            pass

        try:
            if process.stdout:
                process.stdout.close()
        except Exception:
            pass

        try:
            if process.stderr:
                process.stderr.close()
        except Exception:
            pass

    def _emit_error(self, message):
        if self.on_error:
            self.on_error(message)
