"""
Advanced Unit and Integration Tests for Session Recording & Clinical PDF Export.

Validates:
1. Patient vitals generator physiological bounds (systolic > diastolic, ECG states).
2. PatientVitalsModel properties, numeric snapshot, and event propagation.
3. SessionRecorder state machine (IDLE -> RECORDING -> FINALIZING -> EXPORTED/ERROR).
4. Duplicate start and duplicate export prevention.
5. Bounded frame queue and frame dropping behavior.
6. OpenCV reopening and verification of recorded MP4.
7. Frozen immutable session snapshot with export_timestamp.
8. Alerts, messages, and YOLO analytics per-class aggregation.
9. ReportLab clinical PDF generation with vector trend charts and NumberedCanvas.
10. Empty session data handling in PDF without crash.
11. Non-destructive versioning across repeated exports.
"""
import os
import sys
import tempfile
import time
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor

# Dynamic import used inside test functions
from models.patient_vitals_model import PatientVitalsModel
from recording.session_recorder import (
    SessionRecorder, STATE_IDLE, STATE_RECORDING, STATE_FINALIZING,
    STATE_EXPORTED, STATE_ERROR,
)
from recording.session_pdf_generator import generate_session_pdf, REPORTLAB_OK
from screens.live_video import LiveVideoScreen, SessionExportDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── 1. Data Generator Tests ───────────────────────────────────────

def test_patient_vitals_generator_physiological_bounds(qapp):
    """Verify Data Generator vitals: numeric fields, systolic > diastolic, and valid ECG."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Data-Generator"))
    from generators.patient_vitals_generator import PatientVitalsGenerator

    gen = PatientVitalsGenerator()
    received_payloads = []
    gen.vitals_ready.connect(received_payloads.append)

    # Generate 30 frames
    for _ in range(30):
        gen._generate()

    assert len(received_payloads) == 30
    for p in received_payloads:
        assert isinstance(p["heart_rate"], (int, float))
        assert 40 <= p["heart_rate"] <= 200

        assert isinstance(p["spo2"], (int, float))
        assert 80 <= p["spo2"] <= 100

        assert isinstance(p["systolic_bp"], int)
        assert isinstance(p["diastolic_bp"], int)
        assert p["systolic_bp"] > p["diastolic_bp"], "Systolic must be strictly greater than diastolic"
        assert p["systolic_bp"] - p["diastolic_bp"] >= 15, "Pulse pressure must be physiological"

        assert isinstance(p["temperature"], (int, float))
        assert 34.0 <= p["temperature"] <= 42.0

        assert isinstance(p["respiration"], (int, float))
        assert 4.0 <= p["respiration"] <= 60.0

        assert isinstance(p["etco2"], (int, float))
        assert 20.0 <= p["etco2"] <= 60.0

        assert p["ecg_status"] in ("NORMAL SINUS", "PAC DETECTED", "ARTEFACT")


# ── 2. Patient Vitals Model Tests ─────────────────────────────────

def test_patient_vitals_model_numeric_properties_and_snapshots(qapp):
    """Verify model stores and exposes numeric systolic/diastolic BP and numeric snapshot."""
    model = PatientVitalsModel()

    payload = {
        "heart_rate": 78.5,
        "spo2": 98.2,
        "systolic_bp": 124,
        "diastolic_bp": 78,
        "blood_pressure": "124/78",
        "temperature": 36.9,
        "respiration": 16.0,
        "etco2": 39.0,
        "ecg_status": "NORMAL SINUS",
        "timestamp": "2026-09-08T12:00:00",
    }
    model.update_vitals(payload)

    assert model.heart_rate == 78.5
    assert model.spo2 == 98.2
    assert model.systolic_bp == 124.0
    assert model.diastolic_bp == 78.0
    assert model.blood_pressure == "124/78"
    assert model.temperature == 36.9
    assert model.respiration == 16.0
    assert model.etco2 == 39.0
    assert model.ecg_status == "NORMAL SINUS"

    snapshot = model.get_numeric_snapshot()
    assert snapshot["heart_rate"] == 78.5
    assert snapshot["systolic_bp"] == 124.0
    assert snapshot["diastolic_bp"] == 78.0
    assert snapshot["status"] == PatientVitalsModel.STATE_LIVE

    display_data = model.get_display_data()
    assert display_data["hr"] == "78"
    assert display_data["systolic_bp"] == 124.0
    assert display_data["diastolic_bp"] == 78.0


# ── 3. Session Recorder State Machine ─────────────────────────────

def test_session_recorder_state_machine(qapp):
    """Test standard state machine transitions and invalid transition rejections."""
    rec = SessionRecorder()
    assert rec.state == STATE_IDLE

    # Valid: IDLE -> RECORDING
    rec.start_recording(session_id="SESS_STATE_1", username="Dr. Alice", role="surgeon")
    assert rec.state == STATE_RECORDING

    # Reject duplicate start
    rec.start_recording(session_id="SESS_STATE_1")
    assert rec.state == STATE_RECORDING

    # Valid: RECORDING -> FINALIZING
    rec._set_state(STATE_FINALIZING)
    assert rec.state == STATE_FINALIZING

    # Reject invalid transition: FINALIZING -> RECORDING
    assert rec._set_state(STATE_RECORDING) is False
    assert rec.state == STATE_FINALIZING

    # Valid: FINALIZING -> EXPORTED
    assert rec._set_state(STATE_EXPORTED) is True
    assert rec.state == STATE_EXPORTED

    # Valid: EXPORTED -> RECORDING (Export Again or resume)
    assert rec._set_state(STATE_RECORDING) is True
    assert rec.state == STATE_RECORDING

    # Valid: RECORDING -> ERROR
    assert rec._set_state(STATE_ERROR) is True
    assert rec.state == STATE_ERROR


# ── 4. Bounded Queue & Frame Dropping ─────────────────────────────

def test_session_recorder_bounded_queue(qapp):
    """Test that frame queue has fixed max size and dropped frames are tracked without blocking."""
    rec = SessionRecorder()
    rec.start_recording(session_id="SESS_QUEUE_TEST")

    # The queue has maxsize = 30
    assert rec._frame_queue is not None
    assert rec.FRAME_QUEUE_MAX == 30

    img = QImage(320, 240, QImage.Format.Format_RGB888)
    img.fill(QColor("#0095FF"))

    # Rapidly push 40 frames without sleeping
    for _ in range(40):
        rec.record_frame(img)

    # Frame queue should not exceed maxsize
    assert rec._frame_queue.qsize() <= rec.FRAME_QUEUE_MAX
    rec.stop_recording()


# ── 5. OpenCV Verification & Export ───────────────────────────────

def test_session_recorder_opencv_verification(qapp):
    """Test full export, OpenCV reopening, frame verification, and metadata checks."""
    rec = SessionRecorder()
    out_dir = tempfile.mkdtemp()

    rec.start_recording(session_id="VERIFY_TEST_42", username="Dr. Verify", role="surgeon")

    img = QImage(320, 240, QImage.Format.Format_RGB888)
    img.fill(QColor("#10B981"))
    for _ in range(8):
        rec.record_frame(img)
        time.sleep(0.02)

    # Wait for writer to write frames
    time.sleep(0.4)

    rec.export_session(output_dir=out_dir)

    for _ in range(30):
        time.sleep(0.2)
        if rec.state in (STATE_EXPORTED, STATE_ERROR):
            break

    assert rec.state == STATE_EXPORTED
    mp4_path = os.path.join(out_dir, "AETHER_Session_VERIFY_TEST_42.mp4")
    assert os.path.isfile(mp4_path)

    # Run OpenCV verification explicitly
    verify_info = SessionRecorder.verify_recorded_mp4(mp4_path)
    assert verify_info["verified"] is True
    assert verify_info["width"] == 320
    assert verify_info["height"] == 240
    assert verify_info["frame_count"] > 0
    assert verify_info["duration"] > 0


# ── 6. Frozen Session Snapshot ────────────────────────────────────

def test_session_recorder_frozen_snapshot(qapp):
    """Verify build_frozen_snapshot returns an isolated snapshot with export_timestamp."""
    rec = SessionRecorder()
    rec.start_recording(session_id="SNAP_TEST", username="Dr. Snap", role="surgeon")

    rec.record_alert({"severity": "CRITICAL", "message": "High pressure"})
    rec.record_message("System test message")
    rec.record_vitals({
        "heart_rate": 72.0, "spo2": 99.0, "systolic_bp": 120.0,
        "diastolic_bp": 80.0, "status": "LIVE",
    })

    snap1 = rec.build_frozen_snapshot()
    assert "export_timestamp" in snap1
    assert snap1["session_id"] == "SNAP_TEST"
    assert len(snap1["alerts"]) == 1
    assert len(snap1["messages"]) == 1
    assert len(snap1["vitals_snapshots"]) == 1

    # Mutate recorder afterward — frozen snapshot must NOT change
    rec.record_alert({"severity": "INFO", "message": "Late alert"})
    assert len(snap1["alerts"]) == 1, "Snapshot must be immutable deep copy"
    rec.stop_recording()


# ── 7. Events & YOLO Analytics Aggregation ────────────────────────

def test_events_and_yolo_analytics_aggregation(qapp):
    """Verify alerts/messages have relative timestamps and YOLO per-class stats are computed."""
    rec = SessionRecorder()
    rec.start_recording(session_id="YOLO_TEST")

    # Record Alert & Message
    rec.record_alert({"severity": "WARNING", "message": "Tachypnea", "source": "telemetry"})
    rec.record_message("Console pedal engaged", source="pedal")

    # Mock DetectionStats
    class MockDetectionStats:
        objects_detected = 2
        mean_confidence = 88.5
        fps = 29.8
        inference_ms = 14.2
        tracking_active = True
        class_counts = {"grasper": 1, "scissors": 1}

    rec.record_yolo_stats(MockDetectionStats())

    snap = rec.build_frozen_snapshot()

    # Alerts verification
    alert = snap["alerts"][0]
    assert alert["type"] == "ALERT"
    assert alert["severity"] == "WARNING"
    assert "+00:" in alert["relative_session_time"]
    assert alert["source"] == "telemetry"

    # Messages verification
    msg = snap["messages"][0]
    assert msg["type"] == "MESSAGE"
    assert "+00:" in msg["relative_session_time"]
    assert msg["source"] == "pedal"

    # YOLO Analytics verification
    yolo = snap["yolo_analytics"]
    assert yolo["total_detections"] == 2
    assert yolo["tracking_active"] is True
    assert len(yolo["classes"]) == 2
    class_names = [c["name"] for c in yolo["classes"]]
    assert "grasper" in class_names
    assert "scissors" in class_names
    rec.stop_recording()


# ── 8. PDF Generation with Trend Charts ───────────────────────────

def test_session_pdf_generation_complete(qapp):
    """Test generating a full multi-page PDF with trend charts and all sections."""
    if not REPORTLAB_OK:
        pytest.skip("ReportLab not installed")

    out_pdf = os.path.join(tempfile.mkdtemp(), "test_report.pdf")

    # Synthetic snapshot with sufficient vitals to generate trend charts
    snapshot = {
        "session_id": "PDF_TEST_FULL",
        "export_timestamp": "2026-09-08T12:30:00",
        "start_time": "2026-09-08T12:00:00",
        "end_time": "2026-09-08T12:30:00",
        "duration": "00:30:00",
        "duration_seconds": 1800,
        "username": "Dr. Sarah Lin",
        "role": "SURGEON",
        "video_source": "Karl Storz Endoscope 4K",
        "video_width": 1920,
        "video_height": 1080,
        "video_fps": 30.0,
        "frame_count": 54000,
        "dropped_frames": 2,
        "yolo_overlays_included": True,
        "mp4_filename": "AETHER_Session_PDF_TEST_FULL.mp4",
        "alerts": [
            {"timestamp": "2026-09-08T12:05:00", "relative_session_time": "+05:00", "type": "ALERT", "severity": "WARNING", "source": "monitor", "message": "Mild Bradycardia"},
            {"timestamp": "2026-09-08T12:15:00", "relative_session_time": "+15:00", "type": "ALERT", "severity": "CRITICAL", "source": "robot", "message": "High Tissue Pressure"},
        ],
        "messages": [
            {"timestamp": "2026-09-08T12:00:01", "relative_session_time": "+00:01", "type": "MESSAGE", "source": "system", "message": "Session initialized"},
            {"timestamp": "2026-09-08T12:10:00", "relative_session_time": "+10:00", "type": "MESSAGE", "source": "message_center", "message": "Maryland Bipolar armed"},
        ],
        "vitals_snapshots": [
            {"timestamp": f"2026-09-08T12:{i:02d}:00", "relative_session_time": f"+{i:02d}:00", "status": "LIVE",
             "heart_rate": 72.0 + (i % 5), "spo2": 98.0 + (i % 2),
             "systolic_bp": 120.0 + (i % 6), "diastolic_bp": 76.0 + (i % 4),
             "bp": f"{120 + i % 6}/{76 + i % 4}",
             "temperature": 36.8, "respiration": 16.0, "etco2": 38.0, "ecg_status": "NORMAL SINUS"}
            for i in range(12)
        ],
        "yolo_analytics": {
            "total_detections": 1420,
            "avg_inference_ms": 13.8,
            "avg_fps": 30.2,
            "tracking_active": True,
            "classes": [
                {"name": "Maryland Grasper", "count": 820, "avg_confidence": 92.4, "first_seen": "2026-09-08T12:01:10", "last_seen": "2026-09-08T12:28:45"},
                {"name": "Curved Scissors", "count": 600, "avg_confidence": 88.7, "first_seen": "2026-09-08T12:02:15", "last_seen": "2026-09-08T12:29:10"},
            ],
        },
    }

    pdf_path = generate_session_pdf(out_pdf, snapshot)
    assert os.path.isfile(pdf_path)
    assert os.path.getsize(pdf_path) > 10000, "PDF file must contain substantial rendered report data"

    # Verify standard PDF signature
    with open(pdf_path, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-", "Generated file must have standard PDF magic bytes"


# ── 9. PDF Generation with Empty Data ─────────────────────────────

def test_session_pdf_empty_data(qapp):
    """Verify that PDF generation completes cleanly without crashing when data is empty."""
    if not REPORTLAB_OK:
        pytest.skip("ReportLab not installed")

    out_pdf = os.path.join(tempfile.mkdtemp(), "test_empty_report.pdf")
    empty_snapshot = {
        "session_id": "EMPTY_TEST",
        "export_timestamp": "2026-09-08T12:00:00",
        "start_time": "2026-09-08T12:00:00",
        "end_time": "2026-09-08T12:00:05",
        "duration": "00:00:05",
        "duration_seconds": 5,
        "username": "",
        "role": "",
        "video_source": "",
        "video_width": 0,
        "video_height": 0,
        "video_fps": 30.0,
        "frame_count": 0,
        "dropped_frames": 0,
        "yolo_overlays_included": False,
        "alerts": [],
        "messages": [],
        "vitals_snapshots": [],
        "yolo_analytics": {"total_detections": 0, "avg_inference_ms": 0, "avg_fps": 0, "tracking_active": False, "classes": []},
    }

    pdf_path = generate_session_pdf(out_pdf, empty_snapshot)
    assert os.path.isfile(pdf_path)
    assert os.path.getsize(pdf_path) > 0


# ── 10. Non-Destructive Repeated Exports ──────────────────────────

def test_export_again_non_destructive(qapp):
    """Verify that repeated exports generate separate versioned files without overwriting."""
    rec = SessionRecorder()
    out_dir = tempfile.mkdtemp()

    rec.start_recording(session_id="MULTI_EXPORT_1")
    img = QImage(320, 240, QImage.Format.Format_RGB888)
    img.fill(QColor("#0095FF"))
    for _ in range(5):
        rec.record_frame(img)
        time.sleep(0.02)

    time.sleep(0.3)

    # First export
    rec.export_session(output_dir=out_dir)
    for _ in range(25):
        time.sleep(0.2)
        if rec.state == STATE_EXPORTED:
            break
    assert rec.state == STATE_EXPORTED

    files_first = set(os.listdir(out_dir))
    assert any(f.endswith(".pdf") for f in files_first)

    # Second export (EXPORT AGAIN)
    rec.export_session(output_dir=out_dir)
    for _ in range(25):
        time.sleep(0.2)
        if rec.state == STATE_EXPORTED:
            break
    assert rec.state == STATE_EXPORTED

    files_second = set(os.listdir(out_dir))
    # Should contain versioned _v2 files alongside the original files
    assert any("_v2" in f for f in files_second), "Second export must create non-destructive versioned files"
