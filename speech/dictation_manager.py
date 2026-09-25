import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .dictation_worker import DictationWorker

@dataclass
class DictationSegment:
    timestamp: str
    elapsed_seconds: float
    text: str

class DictationManager:
    def __init__(self, model_path: str, on_partial=None, on_final=None, on_error=None):
        self.model_path = model_path
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_error = on_error
        self.worker: Optional[DictationWorker] = None
        self.segments: list[DictationSegment] = []
        self.started_at: Optional[float] = None
        self.paused_at: Optional[float] = None
        self.total_paused_seconds = 0.0
        self.is_running = False
        self.is_paused = False

    def start(self) -> bool:
        if self.is_running:
            return False

        self.segments = []
        self.started_at = time.monotonic()
        self.paused_at = None
        self.total_paused_seconds = 0.0
        self.is_running = True
        self.is_paused = False

        self.worker = DictationWorker(
            self.model_path,
            on_partial=self._on_partial,
            on_final=self._on_final,
            on_error=self._on_error,
        )

        if not self.worker.start():
            self.is_running = False
            self.worker = None
            return False

        return True

    def pause(self):
        if not self.is_running or self.is_paused:
            return

        self.paused_at = time.monotonic()
        self.is_paused = True

        if self.worker:
            self.worker.stop()
            self.worker = None

    def resume(self) -> bool:
        if not self.is_running or not self.is_paused:
            return False

        now = time.monotonic()

        if self.paused_at is not None:
            self.total_paused_seconds += now - self.paused_at

        self.paused_at = None
        self.is_paused = False

        self.worker = DictationWorker(
            self.model_path,
            on_partial=self._on_partial,
            on_final=self._on_final,
            on_error=self._on_error,
        )

        return self.worker.start()

    def stop(self):
        if not self.is_running:
            return

        if self.worker:
            self.worker.stop()
            self.worker = None

        self.is_running = False
        self.is_paused = False
        self.paused_at = None

    def get_segments(self) -> list[DictationSegment]:
        return list(self.segments)

    def get_elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0

        end = time.monotonic()

        if self.paused_at is not None:
            end = self.paused_at

        return max(
            0.0,
            end - self.started_at - self.total_paused_seconds,
        )

    def _on_partial(self, text: str):
        if text and self.on_partial:
            self.on_partial(text)

    def _on_final(self, text: str):
        if not text or not self.is_running or self.is_paused:
            return

        elapsed = self.get_elapsed_seconds()

        segment = DictationSegment(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            elapsed_seconds=elapsed,
            text=text,
        )

        self.segments.append(segment)

        if self.on_final:
            self.on_final(segment)

        print(
            f"DICTATION [{segment.timestamp}] "
            f"[{segment.elapsed_seconds:.1f}s] "
            f"{segment.text}"
        )

    def _on_error(self, message: str):
        if self.on_error:
            self.on_error(message)
        print(f"DICTATION ERROR: {message}")
