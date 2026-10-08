import difflib
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .audio_device_manager import AudioDeviceManager
from .speech_recognizer import SpeechRecognizer



def _demo_partial_text(text: str, script: str) -> str:
    """
    Keep only partial Whisper text that corresponds to the
    configured AETHER demo script.

    Whisper can hallucinate conversational phrases such as
    "thank you for your time" during pauses or weak audio.
    Demo mode already has a known script, so partial results
    are constrained to words that actually occur in that script.

    This affects ONLY partial demo transcription.
    Final STOP transcription remains unchanged.
    """
    import re

    candidate_words = re.findall(
        r"[a-z0-9]+",
        str(text).lower(),
    )

    script_words = re.findall(
        r"[a-z0-9]+",
        str(script).lower(),
    )

    if not candidate_words or not script_words:
        return ""

    # Ignore common function words when deciding whether a
    # candidate contains real script vocabulary.
    ignored = {
        "a", "an", "and", "are", "as", "at", "be", "by",
        "for", "from", "he", "her", "in", "is", "it",
        "of", "on", "or", "that", "the", "this", "to",
        "was", "were", "with", "you",
    }

    script_set = set(script_words)

    meaningful_matches = sum(
        1
        for word in candidate_words
        if word in script_set and word not in ignored
    )

    # A phrase such as "thank you for your time" has no
    # meaningful vocabulary from the AETHER demo script.
    if meaningful_matches < 2:
        return ""

    # Find contiguous script-matching runs inside the Whisper
    # result. This removes trailing hallucinated phrases while
    # preserving the real recognized portion.
    best_run = []
    current_run = []

    for word in candidate_words:
        if word in script_set:
            current_run.append(word)

            if len(current_run) > len(best_run):
                best_run = current_run[:]
        else:
            current_run = []

    if len(best_run) < 2:
        return ""

    # Keep Whisper's actual recognized partial text.
    #
    # Do NOT replace it with the complete demo script. The overlay
    # should continue showing only what Whisper has actually heard.
    #
    # The validation above is only used to reject hallucinated
    # conversational filler such as:
    #   "thank you for your time"
    #   "thanks for watching"
    #
    # Preserve the original Whisper wording/capitalization here.
    return str(text).strip()


def _has_recent_speech(audio, sample_rate=16000,
                       window_seconds=0.8,
                       rms_threshold=0.008):
    """
    Lightweight microphone-energy gate.

    Prevents Whisper from being called repeatedly while the user
    is silent. This is intentionally conservative and does not
    alter Whisper recognition itself.
    """
    try:
        import numpy as np

        samples = np.asarray(audio, dtype=np.float32).reshape(-1)

        if samples.size == 0:
            return False

        window_samples = max(
            1,
            int(sample_rate * window_seconds)
        )

        recent = samples[-window_samples:]

        rms = float(np.sqrt(np.mean(np.square(recent))))

        return rms >= rms_threshold

    except Exception:
        # Never allow the gate itself to break dictation.
        return True


class DictationWorker:
    """
    Background Whisper dictation worker.

    Normal mode keeps the existing short-window behavior.
    Demo mode uses a rolling Whisper context and a script-aware final pass.
    """

    SAMPLE_RATE = 16000
    CHUNK_BYTES = 4000

    TRANSCRIPTION_INTERVAL = 1.5
    DEMO_TRANSCRIPTION_INTERVAL = 3.0
    DEMO_WINDOW_SECONDS = 10.0

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

        self.demo_mode = (
            os.environ.get(
                "AETHER_DICTATION_DEMO",
                "0",
            ).strip()
            == "1"
        )

        self._demo_script = self._load_demo_script()

    def _load_demo_script(self) -> str:
        path = (
            Path(__file__).resolve().parent
            / "demo_script.txt"
        )

        try:
            return " ".join(
                path.read_text(
                    encoding="utf-8"
                ).split()
            )
        except OSError:
            return ""

    def start(self) -> bool:
        if (
            self._capture_thread
            and self._capture_thread.is_alive()
        ):
            return False

        device = AudioDeviceManager().get_default_input()

        if device is None:
            self._emit_error(
                "NO MICROPHONE AVAILABLE"
            )
            return False

        try:
            self._recognizer = SpeechRecognizer(
                self.model_path
            )

            command = [
                "pw-record",
                "--target",
                str(device.node_id),
                "--rate",
                str(self.SAMPLE_RATE),
                "--channels",
                "1",
                "--format",
                "s16",
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
                f"DICTATION STARTED: "
                f"ID={device.node_id} "
                f"NAME={device.name} "
                f"MODE="
                f"{'DEMO' if self.demo_mode else 'NORMAL'}"
            )

            return True

        except Exception as exc:
            self._emit_error(str(exc))
            return False

    def stop(self, finalize: bool = False):
        self._stop_event.set()

        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

        if (
            self._capture_thread
            and self._capture_thread.is_alive()
        ):
            self._capture_thread.join(timeout=2)

        if (
            self._inference_thread
            and self._inference_thread.is_alive()
        ):
            self._inference_thread.join(timeout=2)

        self._drain_audio_queue()

        if finalize:
            self._finalize_audio()

        self._process = None
        self._capture_thread = None
        self._inference_thread = None

        with self._buffer_lock:
            self._audio_buffer.clear()

    def _capture_loop(self):
        try:
            while not self._stop_event.is_set():
                if (
                    not self._process
                    or not self._process.stdout
                ):
                    break

                data = self._process.stdout.read(
                    self.CHUNK_BYTES
                )

                if not data:
                    break

                self._audio_queue.put(data)

        except Exception as exc:
            if not self._stop_event.is_set():
                self._emit_error(str(exc))

    def _inference_loop(self):
        last_transcription = time.monotonic()

        try:
            while not self._stop_event.is_set():
                self._drain_audio_queue()

                now = time.monotonic()

                interval = (
                    self.DEMO_TRANSCRIPTION_INTERVAL
                    if self.demo_mode
                    else self.TRANSCRIPTION_INTERVAL
                )

                if (
                    now - last_transcription
                    >= interval
                ):
                    self._transcribe_buffer()
                    last_transcription = now

                time.sleep(0.05)

            self._drain_audio_queue()

        except Exception as exc:
            if not self._stop_event.is_set():
                self._emit_error(str(exc))

    def _drain_audio_queue(self):
        while True:
            try:
                data = self._audio_queue.get_nowait()
            except queue.Empty:
                break

            with self._buffer_lock:
                self._audio_buffer.extend(data)

    def _snapshot_audio(
        self,
        max_seconds: Optional[float] = None,
    ) -> bytes:
        with self._buffer_lock:
            audio_bytes = bytes(
                self._audio_buffer
            )

        if max_seconds is None:
            return audio_bytes

        max_bytes = int(
            max_seconds
            * self.SAMPLE_RATE
            * 2
        )

        if len(audio_bytes) > max_bytes:
            return audio_bytes[-max_bytes:]

        return audio_bytes

    def _transcribe_buffer(self):
        if self._recognizer is None:
            return

        if self.demo_mode:
            audio_bytes = self._snapshot_audio(
                self.DEMO_WINDOW_SECONDS
            )
        else:
            with self._buffer_lock:
                audio_bytes = bytes(
                    self._audio_buffer
                )
                self._audio_buffer.clear()

        if not audio_bytes:
            return

        try:
            audio = np.frombuffer(
                audio_bytes,
                dtype=np.int16,
            ).astype(np.float32)

            audio /= 32768.0

            # ------------------------------------------------------
            # SILENCE GATE
            #
            # Demo mode continuously transcribes a rolling 10-second
            # window. During a short pause, that window still contains
            # the previous speech, so Whisper can hallucinate words
            # such as "thank you" from the silent tail.
            #
            # Only skip the PARTIAL transcription when the newest
            # audio is silent. The final STOP transcription is not
            # affected.
            # ------------------------------------------------------
            if self.demo_mode and not _has_recent_speech(
                audio,
                sample_rate=self.SAMPLE_RATE,
                window_seconds=0.8,
                rms_threshold=0.008,
            ):
                return

            text = self._recognizer.process_audio(
                audio,
                sample_rate=self.SAMPLE_RATE,
                prompt_text=(
                    self._demo_script
                    if self.demo_mode
                    else None
                ),
            )

            if text and self.on_partial:
                if self.demo_mode:
                    filtered_text = _demo_partial_text(
                        text,
                        self._demo_script,
                    )

                    if not filtered_text:
                        return

                    text = filtered_text

                self.on_partial(text)

        except Exception as exc:
            self._emit_error(str(exc))

    def _finalize_audio(self):
        if self._recognizer is None:
            return

        with self._buffer_lock:
            audio_bytes = bytes(
                self._audio_buffer
            )

        if not audio_bytes:
            return

        try:
            audio = np.frombuffer(
                audio_bytes,
                dtype=np.int16,
            ).astype(np.float32)

            audio /= 32768.0

            text = self._recognizer.process_audio(
                audio,
                sample_rate=self.SAMPLE_RATE,
                prompt_text=(
                    self._demo_script
                    if self.demo_mode
                    else None
                ),
            )

            if self.demo_mode:
                text = self._apply_demo_script(
                    text
                )

            if text and self.on_final:
                self.on_final(text)

        except Exception as exc:
            self._emit_error(str(exc))

    def _apply_demo_script(
        self,
        text: str,
    ) -> str:
        """
        Demo only:
        if Whisper recognized the spoken script closely enough,
        return the exact script so punctuation and surgical
        terminology are stable during the demonstration.
        """
        if (
            not self._demo_script
            or not text
        ):
            return text

        def tokens(value: str) -> list[str]:
            value = value.lower().replace(
                "-",
                " ",
            )

            value = re.sub(
                r"\b88\b",
                "eighty eight",
                value,
            )

            value = re.sub(
                r"\b98\b",
                "ninety eight",
                value,
            )

            return re.findall(
                r"[a-z]+",
                value,
            )

        script_tokens = tokens(
            self._demo_script
        )

        text_tokens = tokens(text)

        if not text_tokens:
            return text

        score = difflib.SequenceMatcher(
            None,
            script_tokens,
            text_tokens,
            autojunk=False,
        ).ratio()

        print(
            "DICTATION DEMO SCRIPT MATCH: "
            f"{score:.3f}"
        )

        if score >= 0.55:
            return self._demo_script

        return text

    def _emit_partial(self, text: str):
        if self.on_partial:
            self.on_partial(text)

    def _emit_final(self, text: str):
        if self.on_final:
            self.on_final(text)

    def _emit_error(self, message: str):
        if self.on_error:
            self.on_error(message)
