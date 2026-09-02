"""
YOLO Detection Pipeline for the Surgical Console.
Handles model loading, video capture, detection, tracking,
inference, and statistics — with AI inference running on a
dedicated background worker thread to ensure the Qt GUI
thread remains 100% responsive at all times.
"""
import os
import queue
import threading
import time
import math
import logging

log = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QFont

# Optional dependencies — graceful degradation
try:
    import cv2
    import numpy as np
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from ultralytics import YOLO
    YOLO_OK = True
except ImportError:
    YOLO_OK = False


class DetectionResult:
    """Single detection bounding box."""
    __slots__ = ("class_name", "confidence", "x1", "y1", "x2", "y2", "track_id")

    def __init__(self, class_name, confidence, x1, y1, x2, y2, track_id=None):
        self.class_name = class_name
        self.confidence = confidence
        self.x1 = int(x1)
        self.y1 = int(y1)
        self.x2 = int(x2)
        self.y2 = int(y2)
        self.track_id = track_id


class DetectionStats:
    """Aggregated stats from a single frame."""
    __slots__ = ("objects_detected", "mean_confidence", "fps", "inference_ms", "tracking_active", "class_counts")

    def __init__(self):
        self.objects_detected = 0
        self.mean_confidence = 0.0
        self.fps = 0.0
        self.inference_ms = 0.0
        self.tracking_active = False
        self.class_counts = {}


class YoloInferenceWorker:
    """Dedicated background worker for executing YOLO inference without blocking the GUI thread."""

    def __init__(self):
        self._model = None
        self._running = False
        self._thread = None
        self._queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._latest_detections = []
        self._latest_stats = DetectionStats()

    def set_model(self, model):
        with self._lock:
            self._model = model

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="YoloInferenceWorker")
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread != threading.current_thread() and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def submit_frame(self, frame_bgr, tracking_enabled: bool):
        if not self._running or self._model is None:
            return
        # Drop older pending frame if inference is currently busy
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait((frame_bgr, tracking_enabled))
        except queue.Full:
            pass

    def get_latest_results(self):
        with self._lock:
            return list(self._latest_detections), self._latest_stats

    def _worker_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is None or not self._running:
                break

            frame_bgr, tracking_enabled = item
            inference_start = time.time()
            detections = []
            stats = DetectionStats()

            try:
                with self._lock:
                    model = self._model

                if model is not None:
                    if tracking_enabled:
                        results = model.track(frame_bgr, persist=True, verbose=False)
                    else:
                        results = model(frame_bgr, verbose=False)

                    stats.inference_ms = (time.time() - inference_start) * 1000

                    if results and len(results) > 0:
                        result = results[0]
                        boxes = result.boxes
                        if boxes is not None and len(boxes) > 0:
                            for box in boxes:
                                cls_id = int(box.cls[0])
                                conf = float(box.conf[0])
                                x1, y1, x2, y2 = box.xyxy[0].tolist()
                                class_name = model.names.get(cls_id, f"class_{cls_id}")
                                track_id = int(box.id[0]) if box.id is not None else None
                                detections.append(DetectionResult(
                                    class_name, conf, x1, y1, x2, y2, track_id
                                ))

                    stats.objects_detected = len(detections)
                    stats.mean_confidence = (
                        sum(d.confidence for d in detections) / len(detections) * 100
                        if detections else 0.0
                    )
                    stats.tracking_active = tracking_enabled
                    for d in detections:
                        stats.class_counts[d.class_name] = stats.class_counts.get(d.class_name, 0) + 1

            except Exception as e:
                log.warning(f"Background YOLO inference error: {e}")
                stats.inference_ms = (time.time() - inference_start) * 1000

            with self._lock:
                self._latest_detections = detections
                self._latest_stats = stats


class YoloPipeline(QObject):
    """Thread-safe YOLO pipeline with background inference and PyQt6 signals."""

    frame_ready = pyqtSignal(QImage)       # Processed frame (with or without local detections)
    raw_frame_ready = pyqtSignal(QImage)   # Clean source frame (without burnt-in overlays, for broadcast)
    stats_updated = pyqtSignal(object)     # DetectionStats
    status_changed = pyqtSignal(str)       # Status message
    model_loaded = pyqtSignal(bool)        # Model load success

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        self._model_loading = False

        self._worker = YoloInferenceWorker()

        self._cap = None
        self._video_running = False
        self._video_path = None
        self._camera_index = None

        self._detection_enabled = False
        self._tracking_enabled = False
        self._vitals_overlay = False

        self._frame_queue = queue.Queue(maxsize=3)
        self._reader_thread = None
        self._process_timer = None

        self._fps_counter = 0
        self._fps_time = time.time()
        self._current_fps = 0.0

        self._video_fps = 30.0
        self._total_frames = 0
        self._current_frame = 0
        self._video_width = 0
        self._video_height = 0

    @property
    def is_model_loaded(self):
        return self._model is not None

    @property
    def detection_enabled(self):
        return self._detection_enabled

    @property
    def tracking_enabled(self):
        return self._tracking_enabled

    @property
    def video_running(self):
        return self._video_running

    # ── Model Management ──────────────────────────────────────────

    def load_model(self, model_name="yolov8x.pt"):
        """Load YOLO model in background thread and initialize inference worker."""
        if self._model or self._model_loading:
            return
        if not YOLO_OK:
            self.status_changed.emit("ultralytics not installed")
            self.model_loaded.emit(False)
            return

        self._model_loading = True
        self.status_changed.emit("Loading YOLO model...")

        def _load():
            try:
                model = YOLO(model_name)
                self._model = model
                self._worker.set_model(model)
                self._worker.start()
                self._model_loading = False
                self.status_changed.emit("YOLO model ready")
                self.model_loaded.emit(True)
            except Exception as ex:
                self._model_loading = False
                self.status_changed.emit(f"Model load failed: {ex}")
                self.model_loaded.emit(False)

        threading.Thread(target=_load, daemon=True).start()

    # ── Video Source ──────────────────────────────────────────────

    def load_video(self, path):
        """Open a video file."""
        if not CV2_OK:
            self.status_changed.emit("OpenCV not installed")
            return False

        self.stop_video()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self.status_changed.emit(f"Cannot open: {os.path.basename(path)}")
            return False

        self._cap = cap
        self._video_path = path
        self._video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._current_frame = 0

        self._start_playback()
        self.status_changed.emit(f"Loaded: {os.path.basename(path)}")
        return True

    def load_camera(self, index=0):
        """Open a camera device with fallback backends and simulated endoscopic feed fallback."""
        if not CV2_OK:
            self.status_changed.emit("OpenCV not installed")
            return False

        self.stop_video()

        # 1. Attempt to open real physical camera device with multiple backends
        cap = None
        backends = [getattr(cv2, "CAP_DSHOW", None), getattr(cv2, "CAP_MSMF", None), cv2.CAP_ANY]
        candidate_indices = [index] + [i for i in (0, 1, 2) if i != index]

        for idx in candidate_indices:
            for b in backends:
                if b is None:
                    continue
                try:
                    c = cv2.VideoCapture(idx, b)
                    if c.isOpened():
                        # Verify we can actually read a test frame
                        ret, test_f = c.read()
                        if ret and test_f is not None and test_f.size > 0:
                            cap = c
                            self._camera_index = idx
                            break
                        else:
                            c.release()
                    else:
                        c.release()
                except Exception:
                    pass
            if cap is not None:
                break

        if cap is not None:
            self._cap = cap
            self._simulated_camera = False
            self._video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._total_frames = 0
            self._video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
            self._video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
            self._start_playback()
            self.status_changed.emit(f"Camera {self._camera_index} active")
            return True

        # 2. If no physical hardware webcam is detected, activate Simulated Endoscopic Camera Feed
        log.info("No physical webcam detected on system; starting simulated endoscopic camera feed.")
        self._cap = None
        self._simulated_camera = True
        self._camera_index = "simulated"
        self._video_fps = 30.0
        self._total_frames = 0
        self._video_width = 1280
        self._video_height = 720

        self._start_playback()
        self.status_changed.emit("Simulated Endoscopic Feed active (no USB webcam found)")
        return True

    def stop(self):
        """Stop playback and release resources (alias for stop_video)."""
        self.stop_video()

    def stop_video(self):
        """Stop playback and release resources."""
        self._video_running = False
        self._simulated_camera = False
        if self._process_timer:
            self._process_timer.stop()
            self._process_timer = None
        if self._reader_thread and self._reader_thread != threading.current_thread() and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None

        if self._cap:
            self._cap.release()
            self._cap = None

        # Stop background inference worker
        self._worker.stop()

        # Clear queue
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

    def _start_playback(self):
        """Begin reader thread and process timer."""
        self._video_running = True
        self._fps_counter = 0
        self._fps_time = time.time()

        if self._model:
            self._worker.start()

        self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
        self._reader_thread.start()

        interval = max(int(1000 / self._video_fps), 16)
        self._process_timer = QTimer()
        self._process_timer.timeout.connect(self._process_frame)
        self._process_timer.start(interval)

    def _read_frames(self):
        """Background thread: reads frames into queue (from camera, video file, or simulated feed)."""
        sim_tick = 0.0
        while self._video_running:
            if getattr(self, "_simulated_camera", False):
                # Generate synthetic realistic laparoscopic endoscopic frame
                w, h = 1280, 720
                frame = np.zeros((h, w, 3), dtype=np.uint8)

                sim_tick += 0.05
                cx = w // 2 + int(math.sin(sim_tick * 0.8) * 120)
                cy = h // 2 + int(math.cos(sim_tick * 0.6) * 60)
                radius = min(w, h) // 2 - 24

                # Draw surgical cavity background
                cv2.circle(frame, (w // 2, h // 2), radius, (24, 28, 85), -1)
                cv2.circle(frame, (w // 2 - 90, h // 2 - 60), radius // 2, (38, 48, 125), -1)
                cv2.circle(frame, (w // 2 + 100, h // 2 + 70), radius // 3, (18, 22, 68), -1)

                # Simulated surgical instrument tip
                cv2.line(frame, (w, h), (cx, cy), (180, 190, 200), 8)
                cv2.circle(frame, (cx, cy), 16, (0, 165, 255), -1)
                cv2.circle(frame, (cx, cy), 8, (255, 255, 255), -1)

                # Scope aperture ring
                cv2.circle(frame, (w // 2, h // 2), radius, (45, 55, 72), 3)

                # HUD Overlay text
                cv2.putText(frame, "ENDOSCOPIC SCOPE [SIMULATED]", (50, 48),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (220, 225, 235), 2)
                cv2.putText(frame, f"REC  FPS 30.0  {time.strftime('%H:%M:%S')}", (50, 84),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 140), 2)

                try:
                    self._frame_queue.put(frame, timeout=0.1)
                except queue.Full:
                    pass
                time.sleep(1.0 / self._video_fps)

            elif self._cap and self._cap.isOpened():
                ret, frame = self._cap.read()
                if not ret:
                    if self._video_path:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        break
                try:
                    self._frame_queue.put(frame, timeout=0.1)
                except queue.Full:
                    pass
                time.sleep(max(1.0 / self._video_fps - 0.005, 0.001))
            else:
                time.sleep(0.05)

    def _process_frame(self):
        """Main thread: process frame from queue without blocking on inference."""
        if self._frame_queue.empty():
            return

        try:
            frame = self._frame_queue.get_nowait()
        except queue.Empty:
            return

        self._current_frame += 1

        # Submit frame to background AI worker if detection is enabled
        detections = []
        stats = DetectionStats()

        if self._detection_enabled and self._model:
            self._worker.submit_frame(frame, self._tracking_enabled)
            detections, worker_stats = self._worker.get_latest_results()
            stats.objects_detected = worker_stats.objects_detected
            stats.mean_confidence = worker_stats.mean_confidence
            stats.inference_ms = worker_stats.inference_ms
            stats.tracking_active = worker_stats.tracking_active
            stats.class_counts = worker_stats.class_counts

        # Calculate FPS
        self._fps_counter += 1
        elapsed = time.time() - self._fps_time
        if elapsed >= 1.0:
            self._current_fps = self._fps_counter / elapsed
            self._fps_counter = 0
            self._fps_time = time.time()
        stats.fps = self._current_fps

        # Convert raw frame to clean QImage for broadcast
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        raw_qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        self.raw_frame_ready.emit(raw_qimg)

        # Make copy of raw_qimg for local display with overlays
        display_qimg = raw_qimg.copy() if detections else raw_qimg

        # Draw detections overlay locally if enabled
        if detections:
            painter = QPainter(display_qimg)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for det in detections:
                color = QColor("#20C997")
                pen = QPen(color, 2)
                painter.setPen(pen)
                painter.setBrush(QColor(0, 0, 0, 0))
                painter.drawRect(int(det.x1), int(det.y1), int(det.x2 - det.x1), int(det.y2 - det.y1))

                # Label background
                label_text = f"{det.class_name.upper()} {det.confidence:.0%}"
                if det.track_id is not None:
                    label_text = f"ID-{det.track_id:03d} {label_text}"
                font = QFont("Inter", 10, QFont.Weight.Bold)
                painter.setFont(font)
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(label_text) + 12
                th = fm.height() + 6
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(int(det.x1), int(det.y1) - th, tw, th)
                painter.setPen(QColor("#0D1117"))
                painter.drawText(int(det.x1) + 6, int(det.y1) - 4, label_text)
            painter.end()

        self.frame_ready.emit(display_qimg)
        self.stats_updated.emit(stats)

    def process_incoming_qimage(self, qimage: QImage):
        """Process an incoming network frame without blocking the GUI thread on AI inference."""
        if qimage is None or qimage.isNull():
            return

        self._current_frame += 1
        self._video_width = qimage.width()
        self._video_height = qimage.height()

        detections = []
        stats = DetectionStats()

        # If detection is enabled and model is loaded, offload frame to background AI worker
        if self._detection_enabled and self._model and CV2_OK:
            try:
                formatted = qimage.convertToFormat(QImage.Format.Format_RGB888)
                w, h = formatted.width(), formatted.height()
                ptr = formatted.bits()
                ptr.setsize(h * w * 3)
                arr = np.frombuffer(ptr, np.uint8).reshape((h, w, 3))
                frame = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

                self._worker.submit_frame(frame, self._tracking_enabled)
                detections, worker_stats = self._worker.get_latest_results()
                stats.objects_detected = worker_stats.objects_detected
                stats.mean_confidence = worker_stats.mean_confidence
                stats.inference_ms = worker_stats.inference_ms
                stats.tracking_active = worker_stats.tracking_active
                stats.class_counts = worker_stats.class_counts

            except Exception as e:
                log.warning(f"Error preparing frame for background YOLO inference: {e}")

        # Calculate FPS
        self._fps_counter += 1
        elapsed = time.time() - self._fps_time
        if elapsed >= 1.0:
            self._current_fps = self._fps_counter / elapsed
            self._fps_counter = 0
            self._fps_time = time.time()
        stats.fps = self._current_fps

        # Make copy of qimage for drawing overlays if needed
        out_img = qimage.copy()

        # Draw detections overlay
        if detections:
            painter = QPainter(out_img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for det in detections:
                color = QColor("#20C997")
                pen = QPen(color, 2)
                painter.setPen(pen)
                painter.setBrush(QColor(0, 0, 0, 0))
                painter.drawRect(int(det.x1), int(det.y1), int(det.x2 - det.x1), int(det.y2 - det.y1))

                # Label background
                label_text = f"{det.class_name.upper()} {det.confidence:.0%}"
                if det.track_id is not None:
                    label_text = f"ID-{det.track_id:03d} {label_text}"
                font = QFont("Inter", 10, QFont.Weight.Bold)
                painter.setFont(font)
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(label_text) + 12
                th = fm.height() + 6
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(int(det.x1), int(det.y1) - th, tw, th)
                painter.setPen(QColor("#0D1117"))
                painter.drawText(int(det.x1) + 6, int(det.y1) - 4, label_text)
            painter.end()

        self.frame_ready.emit(out_img)
        self.stats_updated.emit(stats)

    # ── Control Toggles ───────────────────────────────────────────

    def set_detection(self, enabled):
        self._detection_enabled = enabled
        if enabled and not self._model:
            self.load_model()

    def set_tracking(self, enabled):
        self._tracking_enabled = enabled

    def set_vitals_overlay(self, enabled):
        self._vitals_overlay = enabled

    def get_video_info(self):
        """Return dict with current video metadata."""
        return {
            "width": self._video_width,
            "height": self._video_height,
            "fps": self._video_fps,
            "total_frames": self._total_frames,
            "current_frame": self._current_frame,
            "path": self._video_path,
        }
