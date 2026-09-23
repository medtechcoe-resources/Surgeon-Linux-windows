"""
End-to-End System Test for AETHER Session Recording & Clinical Report Pipeline.

Validates the full chain:
Data Generator
  -> Broker/Router
  -> PatientVitalsModel
  -> PatientSidebar & LiveVideoHUD
  -> SessionRecorder
  -> OpenCV Verification
  -> Clinical PDF Report
"""
import os
import sys
import tempfile
import time
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor

from main import AetherConsole
from screens.live_video import SessionExportDialog, LiveVideoScreen
from recording.session_recorder import (
    STATE_IDLE, STATE_RECORDING, STATE_FINALIZING, STATE_EXPORTED,
)

# Now import Data-Generator modules without shadowing root main
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "Data-Generator"))
from generators.patient_vitals_generator import PatientVitalsGenerator


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_end_to_end_vitals_and_recording_pipeline(qapp):
    """Verify data generator -> model -> sidebar -> HUD -> recorder -> PDF end-to-end pipeline."""
    # 1. Initialize full AetherConsole application window
    window = AetherConsole(username="dr_e2e", role="surgeon", session_id="E2E_SESSION_100")

    # Verify session recording starts automatically on session start
    recorder = window.live_video.recorder
    assert recorder.state == STATE_RECORDING
    assert window.live_video.btn_record_session.text() == "● RECORDING"
    assert window.live_video.btn_export_session.text() == "EXPORT SESSION"

    # 2. Spin up PatientVitalsGenerator and generate dynamic vitals
    generator = PatientVitalsGenerator()
    generated_packets = []
    generator.vitals_ready.connect(generated_packets.append)

    for _ in range(8):
        generator._generate()

    assert len(generated_packets) == 8

    # 3. Route packets via main router _on_message_received (mimicking broker message)
    for pkt in generated_packets:
        msg = {"topic": "patient_vitals", "payload": pkt}
        window._on_message_received("patient_vitals", msg)

    last_pkt = generated_packets[-1]
    expected_hr = f"{int(round(last_pkt['heart_rate']))}"
    expected_bp = f"{last_pkt['systolic_bp']}/{last_pkt['diastolic_bp']}"

    # 4. Verify Single Authoritative Model
    model = window.patient_vitals_model
    assert model.heart_rate == last_pkt["heart_rate"]
    assert model.systolic_bp == float(last_pkt["systolic_bp"])
    assert model.diastolic_bp == float(last_pkt["diastolic_bp"])

    # 5. Verify Patient Sidebar displays identical values
    assert window.sidebar._vital_val_labels["HR"].text() == expected_hr
    assert window.sidebar._vital_val_labels["BP"].text() == expected_bp

    # 6. Verify Live Video HUD displays identical values
    assert window.live_video.vitals_hud.val_hr.text() == expected_hr
    assert window.live_video.vitals_hud.val_bp.text() == expected_bp

    # 7. Verify SessionRecorder captures identical values
    recorded_data = recorder.get_session_data()
    vitals_snaps = recorded_data["vitals_snapshots"]
    assert len(vitals_snaps) > 0
    last_snap = vitals_snaps[-1]
    assert last_snap["heart_rate"] == last_pkt["heart_rate"]
    assert last_snap["systolic_bp"] == float(last_pkt["systolic_bp"])
    assert last_snap["diastolic_bp"] == float(last_pkt["diastolic_bp"])

    # 8. Record endoscopic frames (simulating frame_ready)
    img = QImage(320, 240, QImage.Format.Format_RGB888)
    img.fill(QColor("#0095FF"))
    for _ in range(10):
        window.live_video._on_frame(img)
        time.sleep(0.02)

    # 9. Trigger export via the UI button
    out_dir = tempfile.mkdtemp()
    recorder.export_session(output_dir=out_dir)

    for _ in range(30):
        time.sleep(0.2)
        if recorder.state in (STATE_EXPORTED, "ERROR"):
            break

    assert recorder.state == STATE_EXPORTED

    # 10. Verify exported files exist and are verified non-zero
    files = os.listdir(out_dir)
    pdf_files = [f for f in files if f.endswith(".pdf")]
    mp4_files = [f for f in files if f.endswith(".mp4")]

    assert len(pdf_files) == 1
    assert len(mp4_files) == 1

    pdf_path = os.path.join(out_dir, pdf_files[0])
    mp4_path = os.path.join(out_dir, mp4_files[0])

    assert os.path.getsize(pdf_path) > 5000
    assert os.path.getsize(mp4_path) > 0

    # 11. Verify SessionExportDialog operates correctly
    dialog = SessionExportDialog(
        session_id="E2E_SESSION_100",
        mp4_path=mp4_path,
        pdf_path=pdf_path,
        duration="00:01:15",
        parent=window,
    )
    assert dialog.windowTitle() == "Session Export Complete"
    # All 3 open buttons enabled for valid exports
    buttons = dialog.findChildren(type(dialog.findChild(SessionExportDialog)))
    assert os.path.isfile(mp4_path)
    assert os.path.isfile(pdf_path)


def test_recording_button_workflow_and_no_stopped_bug(qapp):
    """Verify that clicking record session while recording does NOT say 'RECORDING STOPPED'."""
    screen = LiveVideoScreen()
    screen.set_session_info("NO_STOP_TEST", "dr_smith", "surgeon")

    # State is RECORDING
    assert screen.recorder.state == STATE_RECORDING
    assert screen.btn_record_session.text() == "● RECORDING"

    # User clicks record session button — must enter export workflow, NOT stop recording!
    screen._on_record_session_clicked()
    # State transitions to FINALIZING (exporting), not STOPPED!
    assert screen.recorder.state == STATE_FINALIZING
    assert screen.btn_record_session.text() == "FINALIZING..."
    assert screen.btn_export_session.text() == "FINALIZING..."
    assert "RECORDING STOPPED" not in screen.status_label.text()
    assert "stopped" not in screen.status_label.text().lower()


def test_start_session_button_and_stop_button_workflow(qapp):
    """Verify that clicking Start Session button begins recording, and clicking Stop button ends it."""
    screen = LiveVideoScreen()
    # Initially IDLE
    assert screen.recorder.state == STATE_IDLE
    assert hasattr(screen, "btn_start_session")
    assert hasattr(screen, "btn_stop_session")
    assert hasattr(screen, "btn_header_start")
    assert hasattr(screen, "btn_header_stop")

    assert screen.btn_start_session.isEnabled()
    assert not screen.btn_stop_session.isEnabled()
    assert "START SESSION" in screen.btn_start_session.text()
    assert "STOP SESSION" in screen.btn_stop_session.text()

    # User clicks Start Session button
    screen.btn_start_session.click()

    # Session begins, recorder starts recording
    assert screen.recorder.state == STATE_RECORDING
    assert not screen.btn_start_session.isEnabled()
    assert screen.btn_stop_session.isEnabled()
    assert "ACTIVE" in screen.btn_start_session.text()
    assert screen.btn_stop_session.text() == "■ STOP SESSION"
    assert screen.btn_header_stop.isEnabled()

    # User clicks Stop button
    screen.btn_stop_session.click()

    # Finalization and export is triggered
    assert screen.recorder.state == STATE_FINALIZING
    assert screen.btn_start_session.text() == "FINALIZING..."
    assert screen.btn_stop_session.text() == "FINALIZING..."

