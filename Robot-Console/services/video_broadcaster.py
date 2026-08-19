# ═══════════════════════════════════════════════════════════════════
#  ROBOT CONSOLE — VIDEO BROADCAST SERVICE
#  Reads frames from a video file or webcam, compresses to JPEG,
#  base64-encodes, and publishes on the `video_broadcast` topic.
#
#  Low-latency design:
#    - Background thread reads and encodes frames
#    - JPEG quality = 60 (configurable) for wire efficiency
#    - Rate-limited to TARGET_FPS; frames dropped if broker is slow
# ═══════════════════════════════════════════════════════════════════

import threading
import time
import base64
import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage

log = logging.getLogger(__name__)

TARGET_FPS = 15
JPEG_QUALITY = 60
FRAME_INTERVAL = 1.0 / TARGET_FPS


class VideoBroadcastService(QObject):
    """Reads frames from a video file or webcam and publishes them
    via pub-sub on the `video_broadcast` topic.
    """

    status_changed = pyqtSignal(str)
    frame_sent     = pyqtSignal(int)
    fps_updated    = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, conn_manager, parent=None):
        super().__init__(parent)
        self._conn_manager = conn_manager
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._paused  = False
        self._source  = None          # str path or int camera index
        self._frames_sent = 0
        self._fps = 0.0
        self._mode = "none"           # "video" | "camera"
        self._fps_counter = 0

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._update_fps)

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def frames_sent(self) -> int:
        return self._frames_sent

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def mode(self) -> str:
        return self._mode

    # ── Public API ────────────────────────────────────────────────

    def load_video(self, path: str):
        """Load a video file as the broadcast source."""
        self.stop()
        self._source = path
        self._mode   = "video"
        self.status_changed.emit(f"Video loaded: {path}")
        log.info(f"Video loaded: {path}")

    def start_camera(self, index: int = 0):
        """Use webcam as the broadcast source."""
        self.stop()
        self._source = index
        self._mode   = "camera"
        self.status_changed.emit(f"Camera {index} selected")
        log.info(f"Camera {index} selected")

    def start_broadcast(self):
        """Begin reading and publishing frames."""
        if self._running:
            return
        if self._source is None:
            self.error_occurred.emit("No video source — load a video or camera first")
            return
        if not self._conn_manager.is_connected:
            self.error_occurred.emit("Not connected to broker — connect first")
            return

        self._running = True
        self._paused  = False
        self._frames_sent = 0
        self._fps_counter = 0

        self._thread = threading.Thread(
            target=self._broadcast_loop,
            daemon=True,
            name="VideoBroadcast",
        )
        self._thread.start()
        self._fps_timer.start()
        self.status_changed.emit("Broadcasting started")
        log.info("Video broadcast started")

    def stop(self):
        """Stop broadcasting and release resources."""
        self._running = False
        self._paused  = False
        self._fps_timer.stop()
        self._fps = 0.0
        self.fps_updated.emit(0.0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def pause(self):
        self._paused = True
        self.status_changed.emit("Broadcast paused")

    def resume(self):
        self._paused = False
        self.status_changed.emit("Broadcast resumed")

    # ── Background Thread ─────────────────────────────────────────

    def _broadcast_loop(self):
        """Runs in a daemon thread — reads frames and publishes them."""
        try:
            import cv2
        except ImportError:
            self.error_occurred.emit(
                "OpenCV not installed. Run: pip install opencv-python")
            self._running = False
            return

        cap = cv2.VideoCapture(self._source)
        if not cap.isOpened():
            self.error_occurred.emit(f"Cannot open source: {self._source}")
            self._running = False
            return

        width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]

        while self._running:
            if self._paused:
                time.sleep(0.05)
                continue

            t0 = time.perf_counter()

            ret, frame = cap.read()
            if not ret:
                if self._mode == "video":
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self.status_changed.emit("Video looped")
                    continue
                else:
                    break

            # Downscale large frames to keep payloads manageable
            if width > 1280:
                scale = 1280.0 / width
                new_w = 1280
                new_h = int(height * scale)
                frame = cv2.resize(frame, (new_w, new_h),
                                   interpolation=cv2.INTER_LINEAR)

            ok, jpeg_buf = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue

            jpeg_b64 = base64.b64encode(jpeg_buf.tobytes()).decode("ascii")

            try:
                self._conn_manager.publish("video_broadcast", {
                    "frame":       jpeg_b64,
                    "encoding":    "jpeg_b64",
                    "width":       frame.shape[1],
                    "height":      frame.shape[0],
                    "frame_index": self._frames_sent,
                    "timestamp":   datetime.now().isoformat(),
                    "source":      self._mode,
                })
                self._frames_sent += 1
                self._fps_counter += 1
            except Exception as e:
                log.warning(f"Publish failed: {e}")

            # Rate-limit
            elapsed = time.perf_counter() - t0
            sleep_t = FRAME_INTERVAL - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

        cap.release()
        self._running = False
        self.status_changed.emit("Broadcast ended")

    def broadcast_qimage(self, qimage: QImage):
        """Broadcast a QImage directly, bypassing local OpenCV capture."""
        if not self._conn_manager.is_connected:
            return

        t0 = time.perf_counter()

        # Downscale large frames
        if qimage.width() > 1280:
            qimage = qimage.scaledToWidth(1280, Qt.TransformationMode.SmoothTransformation)

        ba = QByteArray()
        buffer = QBuffer(ba)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        qimage.save(buffer, "JPG", JPEG_QUALITY)
        jpeg_b64 = ba.toBase64().data().decode("ascii")

        try:
            self._conn_manager.publish("video_broadcast", {
                "frame":       jpeg_b64,
                "encoding":    "jpeg_b64",
                "width":       qimage.width(),
                "height":      qimage.height(),
                "frame_index": self._frames_sent,
                "timestamp":   datetime.now().isoformat(),
                "source":      self._mode,
            })
            self._frames_sent += 1
            self._fps_counter += 1
        except Exception as e:
            log.warning(f"Publish failed: {e}")

    def _update_fps(self):
        self._fps = float(self._fps_counter)
        self._fps_counter = 0
        self.fps_updated.emit(self._fps)
        self.frame_sent.emit(self._frames_sent)
