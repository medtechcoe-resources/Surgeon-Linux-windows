"""
YOLO Detection Pipeline for the Surgical Console.
Handles model loading, video capture, detection, tracking,
inference, and statistics — all in background threads with
PyQt6 signals for frame/result updates.
"""
import os
import queue
import threading
import time
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


class YoloPipeline(QObject):
    """Thread-safe YOLO pipeline with PyQt6 signals."""

    frame_ready = pyqtSignal(QImage)       # Processed frame (with or without detections)
    stats_updated = pyqtSignal(object)     # DetectionStats
    status_changed = pyqtSignal(str)       # Status message
    model_loaded = pyqtSignal(bool)        # Model load success

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        self._model_loading = False

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
        """Load YOLO model in background thread."""
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
        """Open a camera device."""
        if not CV2_OK:
            self.status_changed.emit("OpenCV not installed")
            return False

        self.stop_video()
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            self.status_changed.emit(f"Cannot open camera {index}")
            return False

        self._cap = cap
        self._camera_index = index
        self._video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._total_frames = 0
        self._video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self._start_playback()
        self.status_changed.emit(f"Camera {index} active")
        return True

    def stop_video(self):
        """Stop playback and release resources."""
        self._video_running = False
        if self._process_timer:
            self._process_timer.stop()
            self._process_timer = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()
            self._cap = None
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

        self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
        self._reader_thread.start()

        interval = max(int(1000 / self._video_fps), 16)
        self._process_timer = QTimer()
        self._process_timer.timeout.connect(self._process_frame)
        self._process_timer.start(interval)

    def _read_frames(self):
        """Background thread: reads frames into queue."""
        while self._video_running and self._cap and self._cap.isOpened():
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

    def _process_frame(self):
        """Main thread: process frame from queue."""
        if self._frame_queue.empty():
            return

        try:
            frame = self._frame_queue.get_nowait()
        except queue.Empty:
            return

        self._current_frame += 1
        detections = []
        stats = DetectionStats()
        inference_start = time.time()

        # Run YOLO if enabled and model loaded
        if self._detection_enabled and self._model:
            try:
                if self._tracking_enabled:
                    results = self._model.track(frame, persist=True, verbose=False)
                else:
                    results = self._model(frame, verbose=False)

                stats.inference_ms = (time.time() - inference_start) * 1000

                if results and len(results) > 0:
                    result = results[0]
                    boxes = result.boxes
                    if boxes is not None and len(boxes) > 0:
                        for box in boxes:
                            cls_id = int(box.cls[0])
                            conf = float(box.conf[0])
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            class_name = self._model.names.get(cls_id, f"class_{cls_id}")
                            track_id = int(box.id[0]) if box.id is not None else None
                            detections.append(DetectionResult(
                                class_name, conf, x1, y1, x2, y2, track_id
                            ))

                stats.objects_detected = len(detections)
                stats.mean_confidence = (
                    sum(d.confidence for d in detections) / len(detections) * 100
                    if detections else 0.0
                )
                stats.tracking_active = self._tracking_enabled
                # Count by class
                for d in detections:
                    stats.class_counts[d.class_name] = stats.class_counts.get(d.class_name, 0) + 1

            except Exception as e:
                log.warning(f"YOLO inference error: {e}")
                stats.inference_ms = (time.time() - inference_start) * 1000

        # Calculate FPS
        self._fps_counter += 1
        elapsed = time.time() - self._fps_time
        if elapsed >= 1.0:
            self._current_fps = self._fps_counter / elapsed
            self._fps_counter = 0
            self._fps_time = time.time()
        stats.fps = self._current_fps

        # Convert frame to QImage
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()

        # Draw detections overlay
        if detections:
            painter = QPainter(qimg)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for det in detections:
                color = QColor("#20C997")
                pen = QPen(color, 2)
                painter.setPen(pen)
                painter.setBrush(QColor(0, 0, 0, 0))
                painter.drawRect(det.x1, det.y1, det.x2 - det.x1, det.y2 - det.y1)

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
                painter.drawRect(det.x1, det.y1 - th, tw, th)
                painter.setPen(QColor("#0D1117"))
                painter.drawText(det.x1 + 6, det.y1 - 4, label_text)
            painter.end()

        # Draw vitals overlay if enabled
        if self._vitals_overlay:
            painter = QPainter(qimg)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Semi-transparent background
            painter.setBrush(QColor(13, 17, 23, 200))
            painter.setPen(QPen(QColor("#2D3748"), 1))
            painter.drawRoundedRect(16, 16, 200, 140, 8, 8)
            # Vitals text
            painter.setPen(QColor("#6B7B8D"))
            font = QFont("Inter", 9, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(28, 38, "VITALS")
            painter.setPen(QColor("#F5F7FA"))
            font = QFont("JetBrains Mono", 10, QFont.Weight.Bold)
            painter.setFont(font)
            vitals = [("HR", "74 bpm"), ("SpO2", "98%"), ("BP", "118/74"), ("EtCO2", "36 mmHg")]
            y = 58
            for label, val in vitals:
                painter.setPen(QColor("#6B7B8D"))
                painter.drawText(28, y, label)
                painter.setPen(QColor("#F5F7FA"))
                painter.drawText(140, y, val)
                y += 24
            painter.end()

        self.frame_ready.emit(qimg)
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
