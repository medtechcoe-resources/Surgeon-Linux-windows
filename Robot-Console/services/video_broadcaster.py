# ═══════════════════════════════════════════════════════════════════
#  ROBOT CONSOLE — VIDEO BROADCAST SERVICE
#  Reads frames from a video file or webcam, compresses to JPEG,
#  and streams binary frames over dedicated TCP Port 5001.
# ═══════════════════════════════════════════════════════════════════

import socket
import ssl
import struct
import threading
import time
import queue
import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage

from shared_networking.config import VIDEO_HOST, VIDEO_PORT, CERTS_DIR
from shared_networking.tls import TLSManager

log = logging.getLogger(__name__)

TARGET_FPS = 15
JPEG_QUALITY = 60
FRAME_INTERVAL = 1.0 / TARGET_FPS
HEADER_FORMAT = "!I"


class VideoBroadcastService(QObject):
    """Reads frames from a video file or webcam and broadcasts them
    via dedicated TCP socket on Port 5001 (binary JPEG format).
    """

    status_changed = pyqtSignal(str)
    frame_sent     = pyqtSignal(int)
    fps_updated    = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, conn_manager=None, host: str = VIDEO_HOST, port: int = VIDEO_PORT,
                 certs_dir: str = CERTS_DIR, use_tls: bool = True, parent=None):
        # Handle cases where parent was passed as second positional argument
        if not isinstance(host, str):
            parent = host
            host = VIDEO_HOST

        super().__init__(parent)
        self._conn_manager = conn_manager
        self._host = str(host)
        self._port = int(port)
        self._certs_dir = certs_dir
        self._use_tls = use_tls
        self._thread: Optional[threading.Thread] = None
        self._send_thread: Optional[threading.Thread] = None
        self._running = False
        self._paused  = False
        self._source  = None          # str path or int camera index
        self._frames_sent = 0
        self._fps = 0.0
        self._mode = "none"           # "video" | "camera"
        self._fps_counter = 0

        self._socket: Optional[socket.socket] = None
        self._socket_lock = threading.Lock()
        self._frame_queue = queue.Queue(maxsize=2)  # Bounded queue to drop stale frames

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._update_fps)

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_broadcasting(self) -> bool:
        return self._running and not self._paused

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
        """Begin reading and publishing frames over TCP Port 5001."""
        if self._running:
            return

        self._running = True
        self._paused  = False
        self._frames_sent = 0
        self._fps_counter = 0

        # Start socket sender thread
        self._send_thread = threading.Thread(
            target=self._socket_sender_loop,
            daemon=True,
            name="VideoSocketSender",
        )
        self._send_thread.start()

        # If source is set and not direct qimage streaming, start local reader loop
        if self._source is not None and self._mode in ("video", "camera"):
            self._thread = threading.Thread(
                target=self._broadcast_loop,
                daemon=True,
                name="VideoBroadcast",
            )
            self._thread.start()

        self._fps_timer.start()
        self.status_changed.emit("Broadcasting started (TCP 5001)")
        log.info("Video broadcast started on TCP Port 5001")

    def stop(self):
        """Stop broadcasting and release resources."""
        self._running = False
        self._paused  = False
        self._fps_timer.stop()
        self._fps = 0.0
        self.fps_updated.emit(0.0)

        # Clear queue
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

        # Close socket
        with self._socket_lock:
            if self._socket:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

        if self._thread and self._thread != threading.current_thread() and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

        if self._send_thread and self._send_thread != threading.current_thread() and self._send_thread.is_alive():
            self._send_thread.join(timeout=1.0)
        self._send_thread = None

    def stop_broadcast(self):
        """Public alias for stop()."""
        self.stop()

    def pause(self):
        self._paused = True
        self.status_changed.emit("Broadcast paused")

    def resume(self):
        self._paused = False
        self.status_changed.emit("Broadcast resumed")

    # ── Socket Management Thread ──────────────────────────────────

    def _socket_sender_loop(self):
        """Worker thread that manages TCP connection to port 5001 and transmits frames."""
        tls_mgr = TLSManager(self._certs_dir)
        reconnect_attempts = 0
        backoffs = [0.5, 1.0, 2.0, 4.0, 8.0, 10.0]

        while self._running:
            # Ensure connected first
            if self._socket is None:
                try:
                    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    raw_sock.settimeout(2.0)

                    if self._use_tls:
                        try:
                            tls_ctx = tls_mgr.create_client_context("robot_console")
                            s = tls_ctx.wrap_socket(raw_sock, server_hostname=None)
                        except Exception as e:
                            log.debug(f"[VideoBroadcaster] TLS context failed ({e}), falling back to raw TCP")
                            s = raw_sock
                    else:
                        s = raw_sock

                    s.connect((self._host, self._port))
                    with self._socket_lock:
                        self._socket = s
                    reconnect_attempts = 0
                    log.info(f"[VideoBroadcaster] Connected to TCP receiver {self._host}:{self._port}")
                    self.status_changed.emit("TCP 5001 Connected")
                except Exception as e:
                    reconnect_attempts += 1
                    delay = backoffs[min(reconnect_attempts - 1, len(backoffs) - 1)]
                    time.sleep(delay)
                    continue

            # Only get frame from queue after socket is connected
            try:
                packet = self._frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            with self._socket_lock:
                if not self._socket or not self._running:
                    continue
                try:
                    self._socket.sendall(packet)
                    self._frames_sent += 1
                    self._fps_counter += 1
                except Exception as e:
                    log.warning(f"[VideoBroadcaster] Socket send error: {e}")
                    try:
                        self._socket.close()
                    except Exception:
                        pass
                    self._socket = None

    def _enqueue_jpeg(self, jpeg_bytes: bytes):
        """Wrap JPEG bytes with 4-byte big-endian length header and enqueue."""
        if not self._running:
            self.start_broadcast()

        header = struct.pack(HEADER_FORMAT, len(jpeg_bytes))
        packet = header + jpeg_bytes

        # Bounded queue: drop oldest frame if full
        if self._frame_queue.full():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self._frame_queue.put_nowait(packet)
        except queue.Full:
            pass

    # ── Background Capture Thread ─────────────────────────────────

    def _broadcast_loop(self):
        """Runs in a daemon thread — reads frames from VideoCapture and sends them."""
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

            self._enqueue_jpeg(jpeg_buf.tobytes())

            # Rate-limit
            elapsed = time.perf_counter() - t0
            sleep_t = FRAME_INTERVAL - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

        cap.release()
        self.status_changed.emit("Broadcast ended")

    def broadcast_qimage(self, qimage: QImage):
        """Broadcast a QImage directly over TCP 5001, bypassing local OpenCV capture."""
        if qimage is None or qimage.isNull():
            return

        if not self._running:
            self._mode = "direct"
            self._source = None
            self.start_broadcast()

        # Downscale large frames if needed (fast)
        if qimage.width() > 1280:
            qimage = qimage.scaledToWidth(1280, Qt.TransformationMode.FastTransformation)

        ba = QByteArray()
        buffer = QBuffer(ba)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        qimage.save(buffer, "JPG", JPEG_QUALITY)
        jpeg_bytes = bytes(ba.data())

        self._enqueue_jpeg(jpeg_bytes)

    def _update_fps(self):
        self._fps = float(self._fps_counter)
        self._fps_counter = 0
        self.fps_updated.emit(self._fps)
        self.frame_sent.emit(self._frames_sent)
