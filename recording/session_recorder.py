"""
Session Recorder — Continuous video recording and session data collection.

Records the processed endoscopic feed (pipeline.frame_ready signal, including YOLO overlays)
from session start. Collects alerts, messages, YOLO detection stats, and patient vitals
throughout the session lifetime.

Maintains an immutable session state machine:
    IDLE        — No active recording.
    RECORDING   — Actively capturing frames and session telemetry.
    FINALIZING  — Export in progress (video finalization, OpenCV verification, frozen snapshot, PDF).
    EXPORTED    — Export completed successfully with verified files.
    ERROR       — Error during recording or export.
"""
import copy
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

log = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    CV2_OK = True
except ImportError:
    CV2_OK = False

# ── Recorder States ───────────────────────────────────────────────

STATE_IDLE = "IDLE"
STATE_RECORDING = "RECORDING"
STATE_FINALIZING = "FINALIZING"
STATE_EXPORTED = "EXPORTED"
STATE_ERROR = "ERROR"

_VALID_TRANSITIONS = {
    STATE_IDLE: {STATE_RECORDING},
    STATE_RECORDING: {STATE_FINALIZING, STATE_ERROR, STATE_IDLE},
    STATE_FINALIZING: {STATE_EXPORTED, STATE_ERROR},
    STATE_EXPORTED: {STATE_RECORDING, STATE_FINALIZING, STATE_IDLE},
    STATE_ERROR: {STATE_RECORDING, STATE_FINALIZING, STATE_IDLE},
}


class SessionRecorder(QObject):
    """Continuous session video recorder and data collector.

    Connects to the YoloPipeline.frame_ready signal to capture the processed
    endoscopic feed (with YOLO overlays if enabled). Collects alerts, messages,
    YOLO stats, and patient vitals as timestamped events.
    """

    # Signals
    export_started = pyqtSignal()
    export_finished = pyqtSignal(str, str)   # (mp4_path, pdf_path)
    export_failed = pyqtSignal(str)          # error message
    state_changed = pyqtSignal(str)          # new state string

    FRAME_QUEUE_MAX = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = STATE_IDLE
        self._lock = threading.Lock()

        # Session metadata
        self._session_id: str = ""
        self._session_start: Optional[datetime] = None
        self._session_end: Optional[datetime] = None
        self._username: str = ""
        self._role: str = ""

        # Video recording
        self._video_writer = None
        self._writer_thread: Optional[threading.Thread] = None
        self._frame_queue: Optional[queue.Queue] = None
        self._writer_running = False
        self._temp_video_path: str = ""
        self._frame_count = 0
        self._dropped_frames = 0
        self._video_width = 0
        self._video_height = 0
        self._video_fps = 30.0
        self._yolo_overlays_included = False
        self._first_frame_received = False

        # Video source info
        self._video_source: str = ""

        # Collected telemetry and events
        self._alerts: List[Dict[str, Any]] = []
        self._messages: List[Dict[str, Any]] = []
        self._vitals_snapshots: List[Dict[str, Any]] = []

        # YOLO session analytics aggregation
        self._yolo_snapshots: List[Dict[str, Any]] = []
        self._yolo_class_stats: Dict[str, Dict[str, Any]] = {}
        self._yolo_inference_times: List[float] = []
        self._yolo_fps_samples: List[float] = []
        self._yolo_tracking_active = False

        # Export count to support multiple non-destructive exports
        self._export_count = 0

    # ── State Management ──────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _set_state(self, new_state: str) -> bool:
        """Transition to a new state if valid. Returns True on success."""
        with self._lock:
            valid_targets = _VALID_TRANSITIONS.get(self._state, set())
            if new_state not in valid_targets:
                log.warning(
                    f"SessionRecorder: Invalid state transition {self._state} -> {new_state}"
                )
                return False
            old = self._state
            self._state = new_state
        log.info(f"SessionRecorder state: {old} -> {new_state}")
        try:
            self.state_changed.emit(new_state)
        except RuntimeError:
            # C++ object was destroyed during shutdown/test teardown
            pass
        return True

    # ── Timing Helper ─────────────────────────────────────────────

    def _get_relative_time(self) -> str:
        """Return formatted relative session time (+MM:SS)."""
        if not self._session_start:
            return "+00:00"
        delta = (datetime.now() - self._session_start).total_seconds()
        delta = max(0.0, delta)
        minutes = int(delta // 60)
        seconds = int(delta % 60)
        return f"+{minutes:02d}:{seconds:02d}"

    # ── Recording Lifecycle ───────────────────────────────────────

    def start_recording(self, session_id: str = "", username: str = "",
                        role: str = "", video_fps: float = 30.0):
        """Begin or resume session recording.

        Initializes at session start. Does not create meaningless video frames
        until the first valid frame_ready frame arrives.
        """
        if self._state == STATE_RECORDING:
            log.info("SessionRecorder: Already recording, ignoring start_recording()")
            return

        # Prepare new or existing session
        with self._lock:
            if not self._session_id:
                self._session_id = session_id or f"S{uuid.uuid4().hex[:8].upper()}"
                self._session_start = datetime.now()
            self._session_end = None
            if username:
                self._username = username
            if role:
                self._role = role
            if video_fps > 0:
                self._video_fps = video_fps

        if not self._set_state(STATE_RECORDING):
            return

        # Initialize temp file path and queue if not active
        if not self._temp_video_path or not os.path.isfile(self._temp_video_path):
            self._temp_video_path = os.path.join(
                tempfile.gettempdir(),
                f"aether_session_{self._session_id}_{int(time.time())}.mp4"
            )

        if self._frame_queue is None:
            self._frame_queue = queue.Queue(maxsize=self.FRAME_QUEUE_MAX)

        if self._writer_thread is None or not self._writer_thread.is_alive():
            self._writer_running = True
            self._writer_thread = threading.Thread(
                target=self._frame_writer_loop,
                daemon=True,
                name="SessionFrameWriter",
            )
            self._writer_thread.start()

        log.info(
            f"SessionRecorder started: session_id={self._session_id}, "
            f"temp_path={self._temp_video_path}"
        )

    def stop_recording(self):
        """Stop recording and flush video writer.

        Preserves all telemetry and recorded session data.
        """
        if self.state != STATE_RECORDING:
            return

        self._session_end = datetime.now()
        self._flush_and_close_video_writer()
        log.info(
            f"SessionRecorder stopped: frames={self._frame_count}, "
            f"dropped={self._dropped_frames}"
        )

    def _flush_and_close_video_writer(self):
        """Signal writer thread to finish, drain queue, and safely release VideoWriter."""
        self._writer_running = False

        if self._frame_queue is not None:
            try:
                self._frame_queue.put_nowait(None)
            except Exception:
                pass

        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5.0)
        self._writer_thread = None

        with self._lock:
            if self._video_writer is not None:
                try:
                    self._video_writer.release()
                except Exception as e:
                    log.warning(f"Error releasing video writer: {e}")
                self._video_writer = None

    # ── Frame Recording ───────────────────────────────────────────

    def record_frame(self, qimage: QImage):
        """Record a processed display frame from pipeline.frame_ready.

        Safely ignores pre-session or null frames.
        Never blocks the GUI thread — drops frames if writer queue is full.
        """
        if self.state != STATE_RECORDING:
            return
        if qimage is None or qimage.isNull():
            return
        if self._frame_queue is None:
            return

        try:
            formatted = qimage.convertToFormat(QImage.Format.Format_RGB888)
            w, h = formatted.width(), formatted.height()
            if w <= 0 or h <= 0:
                return

            ptr = formatted.bits()
            ptr.setsize(h * w * 3)
            rgb = np.frombuffer(ptr, np.uint8).reshape((h, w, 3)).copy()
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            with self._lock:
                if self._video_width == 0:
                    self._video_width = w
                    self._video_height = h
                self._first_frame_received = True

            # Enqueue frame without blocking GUI thread
            try:
                self._frame_queue.put_nowait(bgr)
            except queue.Full:
                self._dropped_frames += 1

        except Exception as e:
            log.debug(f"SessionRecorder: frame conversion error: {e}")

    def _frame_writer_loop(self):
        """Background thread: writes frames from queue to disk."""
        while self._writer_running:
            try:
                frame = self._frame_queue.get(timeout=0.2)
            except (queue.Empty, AttributeError):
                continue

            if frame is None:
                break

            try:
                if self._video_writer is None:
                    h, w = frame.shape[:2]
                    self._video_writer = self._create_video_writer(
                        self._temp_video_path, w, h, self._video_fps
                    )
                    if self._video_writer is None or not self._video_writer.isOpened():
                        log.error("SessionRecorder: Failed to create VideoWriter")
                        self._video_writer = None
                        self._writer_running = False
                        break

                self._video_writer.write(frame)
                self._frame_count += 1
            except Exception as e:
                log.warning(f"SessionRecorder: frame write error: {e}")

        # Drain any remaining frames upon shutdown
        if self._frame_queue is not None:
            while True:
                try:
                    frame = self._frame_queue.get_nowait()
                    if frame is None:
                        break
                    if self._video_writer is not None:
                        self._video_writer.write(frame)
                        self._frame_count += 1
                except (queue.Empty, AttributeError):
                    break
                except Exception:
                    break

    @staticmethod
    def _create_video_writer(path: str, width: int, height: int, fps: float):
        """Create cv2.VideoWriter with codec fallback (mp4v, avc1, XVID)."""
        if not CV2_OK:
            return None

        codecs = [
            ("mp4v", "MPEG-4 Part 2"),
            ("avc1", "H.264"),
            ("XVID", "MPEG-4 ASP"),
        ]

        for codec_str, desc in codecs:
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec_str)
                writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
                if writer.isOpened():
                    log.info(
                        f"SessionRecorder: VideoWriter opened with codec '{codec_str}' ({desc}), "
                        f"{width}x{height}@{fps:.1f}fps -> {path}"
                    )
                    return writer
                writer.release()
            except Exception as e:
                log.warning(f"SessionRecorder: Codec '{codec_str}' failed: {e}")

        log.error("SessionRecorder: No working video codec found")
        return None

    # ── Telemetry & Event Collection ──────────────────────────────

    def record_alert(self, alert: dict):
        """Record an alert event with timestamp, relative time, severity, and source."""
        if self.state not in (STATE_RECORDING, STATE_IDLE):
            return
        if not isinstance(alert, dict):
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "relative_session_time": self._get_relative_time(),
            "type": "ALERT",
            "severity": str(alert.get("severity", "INFO")).upper(),
            "message": str(alert.get("message", "System notification")),
            "source": str(alert.get("source", alert.get("subsystem", "alert_generator"))),
        }
        with self._lock:
            self._alerts.append(entry)

    def record_message(self, text: str, source: str = "system", severity: str = "INFO"):
        """Record a Message Center message with timestamp, relative time, severity, and source."""
        if self.state not in (STATE_RECORDING, STATE_IDLE):
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "relative_session_time": self._get_relative_time(),
            "type": "MESSAGE",
            "severity": str(severity).upper(),
            "message": str(text),
            "source": str(source),
        }
        with self._lock:
            self._messages.append(entry)

    def record_yolo_stats(self, stats):
        """Aggregate YOLO detection statistics from a DetectionStats object."""
        if self.state != STATE_RECORDING or stats is None:
            return

        now_iso = datetime.now().isoformat()
        objs = getattr(stats, "objects_detected", 0)
        mean_conf = getattr(stats, "mean_confidence", 0.0)
        fps = getattr(stats, "fps", 0.0)
        inf_ms = getattr(stats, "inference_ms", 0.0)
        track_active = getattr(stats, "tracking_active", False)
        class_counts = dict(getattr(stats, "class_counts", {}))

        with self._lock:
            if objs > 0:
                self._yolo_overlays_included = True

            if track_active:
                self._yolo_tracking_active = True

            if inf_ms > 0:
                self._yolo_inference_times.append(inf_ms)
            if fps > 0:
                self._yolo_fps_samples.append(fps)

            # Update per-class aggregation
            for cls_name, cnt in class_counts.items():
                if cls_name not in self._yolo_class_stats:
                    self._yolo_class_stats[cls_name] = {
                        "count": 0,
                        "confidence_sum": 0.0,
                        "confidence_samples": 0,
                        "first_seen": now_iso,
                        "last_seen": now_iso,
                    }
                item = self._yolo_class_stats[cls_name]
                item["count"] += cnt
                item["last_seen"] = now_iso
                if mean_conf > 0:
                    item["confidence_sum"] += mean_conf
                    item["confidence_samples"] += 1

            self._yolo_snapshots.append({
                "timestamp": now_iso,
                "objects_detected": objs,
                "mean_confidence": mean_conf,
                "fps": fps,
                "inference_ms": inf_ms,
                "tracking_active": track_active,
                "class_counts": class_counts,
            })

    def record_vitals(self, display_data: dict):
        """Record patient vitals snapshot from PatientVitalsModel.

        Extracts numeric values, preserves systolic and diastolic separation,
        and avoids recording duplicate consecutive points.
        """
        if self.state != STATE_RECORDING or not isinstance(display_data, dict):
            return

        status = display_data.get("status", "NO DATA")
        if status in ("NO DATA", "DISCONNECTED", "INVALID"):
            return

        def _to_float(val):
            if val is None or val == "--":
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        hr = _to_float(display_data.get("heart_rate", display_data.get("hr")))
        spo2 = _to_float(display_data.get("spo2"))
        temp = _to_float(display_data.get("temperature"))
        resp = _to_float(display_data.get("respiration"))
        etco2 = _to_float(display_data.get("etco2"))
        ecg_status = str(display_data.get("ecg_status", "NORMAL SINUS"))

        # Systolic & Diastolic BP
        sys_bp = _to_float(display_data.get("systolic_bp", display_data.get("sys")))
        dia_bp = _to_float(display_data.get("diastolic_bp", display_data.get("dia")))

        bp_str = str(display_data.get("bp", "--"))
        if (sys_bp is None or dia_bp is None) and "/" in bp_str:
            parts = bp_str.split("/")
            if len(parts) == 2:
                sys_bp = _to_float(parts[0])
                dia_bp = _to_float(parts[1])

        # Deduplicate identical consecutive snapshots
        with self._lock:
            if self._vitals_snapshots:
                last = self._vitals_snapshots[-1]
                if (last.get("hr") == hr and last.get("spo2") == spo2 and
                        last.get("systolic_bp") == sys_bp and last.get("diastolic_bp") == dia_bp and
                        last.get("temperature") == temp and last.get("respiration") == resp and
                        last.get("etco2") == etco2 and last.get("ecg_status") == ecg_status):
                    return

            self._vitals_snapshots.append({
                "timestamp": datetime.now().isoformat(),
                "relative_session_time": self._get_relative_time(),
                "status": status,
                "heart_rate": hr,
                "hr": hr,
                "spo2": spo2,
                "systolic_bp": sys_bp,
                "diastolic_bp": dia_bp,
                "bp": f"{int(round(sys_bp))}/{int(round(dia_bp))}" if (sys_bp is not None and dia_bp is not None) else bp_str,
                "temperature": temp,
                "respiration": resp,
                "etco2": etco2,
                "ecg_status": ecg_status,
            })

    def update_video_info(self, info: dict):
        """Update video source metadata from pipeline.get_video_info()."""
        if not isinstance(info, dict):
            return
        with self._lock:
            self._video_source = info.get("path", "") or ""
            if info.get("fps"):
                self._video_fps = info["fps"]

    # ── Session Snapshot ──────────────────────────────────────────

    def build_frozen_snapshot(self) -> Dict[str, Any]:
        """Build an immutable snapshot of all session data with an export timestamp."""
        with self._lock:
            end_time = self._session_end or datetime.now()
            start_time = self._session_start or end_time
            duration_seconds = max(0.0, (end_time - start_time).total_seconds())

            hours = int(duration_seconds // 3600)
            minutes = int((duration_seconds % 3600) // 60)
            seconds = int(duration_seconds % 60)
            duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            # Format YOLO analytics
            class_table = []
            for cls_name, data in sorted(self._yolo_class_stats.items(), key=lambda x: -x[1]["count"]):
                cnt = data["count"]
                avg_conf = (data["confidence_sum"] / data["confidence_samples"]) if data["confidence_samples"] > 0 else 0.0
                class_table.append({
                    "name": cls_name,
                    "count": cnt,
                    "avg_confidence": avg_conf,
                    "first_seen": data["first_seen"],
                    "last_seen": data["last_seen"],
                })

            avg_inf = (sum(self._yolo_inference_times) / len(self._yolo_inference_times)) if self._yolo_inference_times else 0.0
            avg_fps = (sum(self._yolo_fps_samples) / len(self._yolo_fps_samples)) if self._yolo_fps_samples else 0.0
            total_yolo_detections = sum(d["count"] for d in class_table)

            snapshot = {
                "session_id": self._session_id,
                "export_timestamp": datetime.now().isoformat(),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration": duration_str,
                "duration_seconds": duration_seconds,
                "username": self._username,
                "role": self._role,

                # Video details
                "video_source": self._video_source,
                "video_width": self._video_width,
                "video_height": self._video_height,
                "video_fps": self._video_fps,
                "frame_count": self._frame_count,
                "dropped_frames": self._dropped_frames,
                "yolo_overlays_included": self._yolo_overlays_included,

                # Events & Telemetry
                "alerts": copy.deepcopy(self._alerts),
                "messages": copy.deepcopy(self._messages),
                "vitals_snapshots": copy.deepcopy(self._vitals_snapshots),
                "yolo_snapshots": copy.deepcopy(self._yolo_snapshots),

                # YOLO Aggregations
                "yolo_analytics": {
                    "total_detections": total_yolo_detections,
                    "avg_inference_ms": avg_inf,
                    "avg_fps": avg_fps,
                    "tracking_active": self._yolo_tracking_active,
                    "classes": class_table,
                },
            }
            return snapshot

    def get_session_data(self) -> Dict[str, Any]:
        """Convenience alias for build_frozen_snapshot()."""
        return self.build_frozen_snapshot()

    # ── OpenCV Verification ───────────────────────────────────────

    @staticmethod
    def verify_recorded_mp4(video_path: str) -> Dict[str, Any]:
        """Verify finalized MP4 by reopening with OpenCV and checking frame validity.

        Returns dict with status, width, height, fps, frame_count, duration.
        Raises RuntimeError on verification failure.
        """
        if not os.path.isfile(video_path):
            raise RuntimeError(f"MP4 file does not exist: {video_path}")

        file_size = os.path.getsize(video_path)
        if file_size == 0:
            raise RuntimeError(f"MP4 file is empty (0 bytes): {video_path}")

        if not CV2_OK:
            log.warning("OpenCV not available — skipping frame-by-frame verification")
            return {
                "verified": True,
                "file_size": file_size,
                "width": 0, "height": 0, "fps": 0, "frame_count": 0, "duration": 0,
            }

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV could not open recorded MP4: {video_path}")

        try:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            # Read first frame to verify decoder readable
            ret, frame = cap.read()
            if not ret or frame is None:
                raise RuntimeError("Recorded MP4 contains no readable video frames")

            duration = (total_frames / fps) if (fps > 0 and total_frames > 0) else 0.0

            log.info(
                f"SessionRecorder: MP4 verified successfully — {w}x{h} @ {fps:.1f}fps, "
                f"{total_frames} frames, {duration:.1f}s, {file_size} bytes"
            )
            return {
                "verified": True,
                "file_size": file_size,
                "width": w,
                "height": h,
                "fps": fps,
                "frame_count": total_frames,
                "duration": duration,
            }
        finally:
            cap.release()

    # ── Export ─────────────────────────────────────────────────────

    def export_session(self, output_dir: str = ""):
        """Export session recording (MP4 + PDF) in background thread.

        Prevents duplicate export operations while exporting.
        """
        if self.state == STATE_FINALIZING:
            log.warning("SessionRecorder: Export already in progress")
            return

        # Stop recording first to finalize writer
        if self.state == STATE_RECORDING:
            self.stop_recording()

        if not self._set_state(STATE_FINALIZING):
            self.export_failed.emit("Invalid state for export")
            return

        self.export_started.emit()

        if not output_dir:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "exports", "sessions", self._session_id,
            )
        else:
            base_dir = output_dir

        thread = threading.Thread(
            target=self._export_worker,
            args=(base_dir,),
            daemon=True,
            name="SessionExportWorker",
        )
        thread.start()

    def _export_worker(self, output_dir: str):
        """Background worker: verifies MP4, captures frozen snapshot, generates PDF."""
        try:
            os.makedirs(output_dir, exist_ok=True)
            self._export_count += 1

            # Suffix if multiple exports exist to prevent accidental overwrite
            suffix = f"_v{self._export_count}" if self._export_count > 1 else ""
            mp4_filename = f"AETHER_Session_{self._session_id}{suffix}.mp4"
            pdf_filename = f"AETHER_Session_{self._session_id}{suffix}_Summary.pdf"

            mp4_path = os.path.join(output_dir, mp4_filename)
            pdf_path = os.path.join(output_dir, pdf_filename)

            # Move temp video to export directory if recorded
            video_verified = False
            video_info: Dict[str, Any] = {}

            if self._temp_video_path and os.path.isfile(self._temp_video_path):
                # Copy instead of move if exporting multiple times
                shutil.copy2(self._temp_video_path, mp4_path)
                try:
                    video_info = self.verify_recorded_mp4(mp4_path)
                    video_verified = True
                except Exception as ve:
                    log.error(f"SessionRecorder: MP4 verification failed: {ve}")
                    video_verified = False
            else:
                mp4_path = ""
                log.warning("SessionRecorder: No temp video frames recorded")

            # Capture frozen immutable session snapshot
            snapshot = self.build_frozen_snapshot()
            snapshot["mp4_filename"] = mp4_filename if (mp4_path and video_verified) else "No video recorded"
            snapshot["mp4_path"] = mp4_path if video_verified else ""
            if video_verified and video_info.get("frame_count", 0) > 0:
                snapshot["frame_count"] = video_info["frame_count"]
                snapshot["video_width"] = video_info.get("width", snapshot["video_width"])
                snapshot["video_height"] = video_info.get("height", snapshot["video_height"])
                snapshot["video_fps"] = video_info.get("fps", snapshot["video_fps"])

            # Generate clinical PDF report from frozen snapshot
            from recording.session_pdf_generator import generate_session_pdf
            generate_session_pdf(pdf_path, snapshot)

            if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
                raise RuntimeError("PDF generation failed: File is missing or empty")

            # Final verification
            if mp4_path and not video_verified:
                raise RuntimeError("Video verification failed — recorded MP4 is invalid")

            self._set_state(STATE_EXPORTED)
            try:
                self.export_finished.emit(mp4_path if video_verified else "", pdf_path)
            except RuntimeError:
                pass
            log.info(f"SessionRecorder: Export complete — MP4={mp4_path}, PDF={pdf_path}")

        except Exception as e:
            if isinstance(e, RuntimeError) and "wrapped C/C++ object" in str(e):
                return
            log.error(f"SessionRecorder: Export failed: {e}", exc_info=True)
            self._set_state(STATE_ERROR)
            try:
                self.export_failed.emit(str(e))
            except RuntimeError:
                pass

    def reset(self):
        """Reset recorder to IDLE state for a clean session."""
        if self.state in (STATE_RECORDING, STATE_FINALIZING):
            self.stop_recording()

        if self._temp_video_path and os.path.isfile(self._temp_video_path):
            try:
                os.remove(self._temp_video_path)
            except Exception:
                pass

        with self._lock:
            self._state = STATE_IDLE
            self._session_id = ""
            self._session_start = None
            self._session_end = None
            self._alerts.clear()
            self._messages.clear()
            self._yolo_snapshots.clear()
            self._yolo_class_stats.clear()
            self._yolo_inference_times.clear()
            self._yolo_fps_samples.clear()
            self._yolo_tracking_active = False
            self._vitals_snapshots.clear()
            self._frame_count = 0
            self._dropped_frames = 0
            self._first_frame_received = False
            self._export_count = 0

        self.state_changed.emit(STATE_IDLE)
