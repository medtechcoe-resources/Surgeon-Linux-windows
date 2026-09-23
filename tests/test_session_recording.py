"""
Tests for Session Recording and Summary Export feature in Live Video.
"""
import os
import tempfile
import time
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor

from recording.session_recorder import (
    SessionRecorder, STATE_IDLE, STATE_RECORDING, STATE_FINALIZING,
    STATE_EXPORTED,
)
from screens.live_video import LiveVideoScreen


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_session_buttons_present(qapp):
    """Verify RECORD SESSION and EXPORT SESSION buttons exist in LiveVideoScreen."""
    screen = LiveVideoScreen()
    assert hasattr(screen, "btn_record_session"), "RECORD SESSION button not found"
    assert hasattr(screen, "btn_export_session"), "EXPORT SESSION button not found"
    assert hasattr(screen, "btn_header_export"), "Header EXPORT button not found"
    assert hasattr(screen, "recorder"), "SessionRecorder instance not found"
    assert "RECORD SESSION" in screen.btn_record_session.text()
    assert "EXPORT SESSION" in screen.btn_export_session.text()


def test_session_recorder_lifecycle(qapp):
    """Test SessionRecorder state transitions and data collection."""
    recorder = SessionRecorder()
    assert recorder.state == STATE_IDLE

    # Start recording
    recorder.start_recording(session_id="SESS_TEST", username="Dr. Test", role="surgeon")
    assert recorder.state == STATE_RECORDING

    # Record alert
    recorder.record_alert({"severity": "CRITICAL", "message": "Test alert"})

    # Record message
    recorder.record_message("Test message in center")

    # Record vitals
    recorder.record_vitals({"hr": "78", "spo2": "99", "bp": "120/80", "status": "LIVE"})

    data = recorder.get_session_data()
    assert data["session_id"] == "SESS_TEST"
    assert len(data["alerts"]) == 1
    assert len(data["messages"]) == 1
    assert len(data["vitals_snapshots"]) == 1


def test_session_export_mp4_and_pdf(qapp):
    """Test full end-to-end export generating both MP4 and PDF."""
    recorder = SessionRecorder()
    out_dir = tempfile.mkdtemp()

    recorder.start_recording(session_id="EXPORT_TEST_99", username="Dr. Export", role="surgeon")

    # Feed test frames
    img = QImage(320, 240, QImage.Format.Format_RGB888)
    img.fill(QColor("#0095FF"))
    for _ in range(5):
        recorder.record_frame(img)
        time.sleep(0.02)

    recorder.record_alert({"severity": "WARNING", "message": "Arrhythmia detected"})
    recorder.record_message("Procedure initiated")
    recorder.record_vitals({
        "hr": "80", "spo2": "98", "bp": "118/76",
        "temperature": "36.8", "status": "LIVE",
    })

    # Allow background writer thread to write frames
    time.sleep(0.4)

    # Export
    recorder.export_session(output_dir=out_dir)

    # Wait for export completion
    for _ in range(30):
        time.sleep(0.2)
        if recorder.state in (STATE_EXPORTED, "ERROR"):
            break

    assert recorder.state == STATE_EXPORTED, f"Export failed with state: {recorder.state}"
    files = os.listdir(out_dir)
    assert any(f.endswith(".pdf") for f in files), "PDF file was not exported"
    assert any(f.endswith(".mp4") for f in files), "MP4 file was not exported"
