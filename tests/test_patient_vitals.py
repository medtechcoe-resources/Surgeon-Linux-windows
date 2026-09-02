"""
Tests for Patient Vitals Model, Message Routing, Patient Sidebar, and Live Video HUD.
Validates authoritative model, state machine, data consistency, and UI behavior.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
import pytest

@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

from models.patient_vitals_model import PatientVitalsModel
from widgets.patient_sidebar import PatientSidebar
from screens.live_video import LiveVideoScreen
from main import AetherConsole


class TestPatientVitalsModel:
    def test_initial_state_is_no_data(self):
        model = PatientVitalsModel()
        assert model.state == PatientVitalsModel.STATE_NO_DATA
        data = model.get_display_data()
        assert data["hr"] == "--"
        assert data["spo2"] == "--"
        assert data["bp"] == "--"
        assert data["temperature"] == "--"
        assert data["status"] == "NO DATA"
        assert data["is_live"] is False

    def test_valid_message_transitions_to_live(self):
        model = PatientVitalsModel()
        payload = {
            "heart_rate": 74.2,
            "spo2": 98.1,
            "blood_pressure": "118/74",
            "temperature": 36.8,
            "respiration": 16.0,
            "etco2": 38.0,
            "ecg_status": "NORMAL SINUS",
            "timestamp": "2026-08-31T12:00:00",
        }
        model.update_vitals(payload)
        assert model.state == PatientVitalsModel.STATE_LIVE
        data = model.get_display_data()
        assert data["hr"] == "74"
        assert data["spo2"] == "98"
        assert data["bp"] == "118/74"
        assert data["temperature"] == "36.8"
        assert data["status"] == "LIVE"
        assert data["is_live"] is True

    def test_missing_optional_fields_does_not_invalidate_message(self):
        model = PatientVitalsModel()
        # Payload with optional fields (respiration, etco2, ecg_status) omitted
        payload = {
            "heart_rate": 78,
            "spo2": 99,
            "blood_pressure": "120/80",
            "temperature": 37.0,
        }
        model.update_vitals(payload)
        assert model.state == PatientVitalsModel.STATE_LIVE
        data = model.get_display_data()
        assert data["hr"] == "78"
        assert data["spo2"] == "99"
        assert data["bp"] == "120/80"
        assert data["temperature"] == "37.0"
        assert data["respiration"] == "--"
        assert data["etco2"] == "--"

    def test_alternative_key_support(self):
        model = PatientVitalsModel()
        payload = {
            "hr": 80.0,
            "oxygen_saturation": 97.0,
            "bp": "115/75",
            "temp": 36.6,
        }
        model.update_vitals(payload)
        assert model.state == PatientVitalsModel.STATE_LIVE
        data = model.get_display_data()
        assert data["hr"] == "80"
        assert data["spo2"] == "97"
        assert data["bp"] == "115/75"
        assert data["temperature"] == "36.6"

    def test_invalid_data_handling(self):
        model = PatientVitalsModel()
        # Non-dict payload
        model.update_vitals("not a dict")
        assert model.state == PatientVitalsModel.STATE_INVALID

        # Out-of-range heart rate
        model.update_vitals({"heart_rate": -10, "spo2": 98, "blood_pressure": "120/80", "temperature": 37.0})
        assert model.state == PatientVitalsModel.STATE_INVALID

        # Malformed blood pressure
        model.update_vitals({"heart_rate": 75, "spo2": 98, "blood_pressure": "high", "temperature": 37.0})
        assert model.state == PatientVitalsModel.STATE_INVALID

    def test_state_transition_live_to_stale_and_stale_to_live(self):
        model = PatientVitalsModel()
        payload = {
            "heart_rate": 74.0,
            "spo2": 98.0,
            "blood_pressure": "118/74",
            "temperature": 36.8,
        }
        model.update_vitals(payload)
        assert model.state == PatientVitalsModel.STATE_LIVE

        # Trigger watchdog timeout manually to test transition to STALE
        model._on_watchdog_timeout()
        assert model.state == PatientVitalsModel.STATE_STALE
        stale_data = model.get_display_data()
        assert stale_data["status"] == "STALE"
        assert stale_data["is_stale"] is True

        # Next valid message transitions STALE -> LIVE
        model.update_vitals({
            "heart_rate": 76.0,
            "spo2": 98.0,
            "blood_pressure": "118/74",
            "temperature": 36.8,
        })
        assert model.state == PatientVitalsModel.STATE_LIVE
        assert model.get_display_data()["status"] == "LIVE"

    def test_disconnection_and_resumption(self):
        model = PatientVitalsModel()
        model.update_vitals({
            "heart_rate": 74.0,
            "spo2": 98.0,
            "blood_pressure": "118/74",
            "temperature": 36.8,
        })
        assert model.state == PatientVitalsModel.STATE_LIVE

        # Simulate broker disconnect
        model.set_disconnected()
        assert model.state == PatientVitalsModel.STATE_DISCONNECTED
        disc_data = model.get_display_data()
        assert disc_data["status"] == "DISCONNECTED"
        assert disc_data["hr"] == "--"
        assert disc_data["spo2"] == "--"
        assert disc_data["bp"] == "--"
        assert disc_data["temperature"] == "--"

        # Resuming valid data transitions back to LIVE
        model.update_vitals({
            "heart_rate": 80.0,
            "spo2": 99.0,
            "blood_pressure": "120/78",
            "temperature": 36.9,
        })
        assert model.state == PatientVitalsModel.STATE_LIVE
        resumed_data = model.get_display_data()
        assert resumed_data["hr"] == "80"
        assert resumed_data["status"] == "LIVE"


class TestPatientSidebarIntegration:
    def test_sidebar_initial_state_has_no_hardcoded_vitals(self):
        sidebar = PatientSidebar()
        assert sidebar._vital_val_labels["HR"].text() == "--"
        assert sidebar._vital_val_labels["SpO2"].text() == "--"
        assert sidebar._vital_val_labels["BP"].text() == "--"
        assert sidebar._vital_val_labels["Temp"].text() == "--"
        assert sidebar._vitals_status_label.text() == "NO DATA"

    def test_sidebar_updates_from_model(self):
        sidebar = PatientSidebar()
        model = PatientVitalsModel()
        sidebar.set_vitals_model(model)

        # Before any data
        assert sidebar._vital_val_labels["HR"].text() == "--"

        # Emit data
        model.update_vitals({
            "heart_rate": 75.0,
            "spo2": 98.0,
            "blood_pressure": "118/74",
            "temperature": 36.8,
        })
        assert sidebar._vital_val_labels["HR"].text() == "75"
        assert sidebar._vital_val_labels["SpO2"].text() == "98"
        assert sidebar._vital_val_labels["BP"].text() == "118/74"
        assert sidebar._vital_val_labels["Temp"].text() == "36.8"
        assert sidebar._vitals_status_label.text() == "LIVE"

        # Disconnection
        model.set_disconnected()
        assert sidebar._vital_val_labels["HR"].text() == "--"
        assert sidebar._vitals_status_label.text() == "DISCONNECTED"


class TestLiveVideoHUDIntegration:
    def test_hud_initial_state_and_dimensions(self):
        screen = LiveVideoScreen(mode="surgeon")
        hud = screen.vitals_hud
        assert hud is not None
        assert hud.width() == 286
        assert hud.height() == 62
        assert hud.val_hr.text() == "--"
        assert hud.val_spo2.text() == "--"
        assert hud.val_bp.text() == "--"
        assert hud.val_temp.text() == "--"
        assert hud.status_badge.text() == "NO DATA"
        # Initially hidden until toggled
        assert hud.isVisible() is False

    def test_hud_toggle_visibility(self):
        screen = LiveVideoScreen(mode="surgeon")
        screen.show()
        hud = screen.vitals_hud
        assert screen._vitals_enabled is False
        assert hud.isHidden() is True

        # Toggle on
        screen._toggle_vitals()
        assert screen._vitals_enabled is True
        assert hud.isHidden() is False

        # Toggle off
        screen._toggle_vitals()
        assert screen._vitals_enabled is False
        assert hud.isHidden() is True
        screen.close()

    def test_hud_updates_from_model(self):
        screen = LiveVideoScreen(mode="surgeon")
        model = PatientVitalsModel()
        screen.set_vitals_model(model)

        model.update_vitals({
            "heart_rate": 72.0,
            "spo2": 99.0,
            "blood_pressure": "116/72",
            "temperature": 36.7,
        })
        assert screen.vitals_hud.val_hr.text() == "72"
        assert screen.vitals_hud.val_spo2.text() == "99"
        assert screen.vitals_hud.val_bp.text() == "116/72"
        assert screen.vitals_hud.val_temp.text() == "36.7"
        assert screen.vitals_hud.status_badge.text() == "LIVE"


class TestVitalsConsistencyAndRouting:
    def test_sidebar_and_hud_consistency(self):
        """Verify that Patient Sidebar and Live Video HUD consume the exact same authoritative data."""
        model = PatientVitalsModel()
        sidebar = PatientSidebar()
        screen = LiveVideoScreen(mode="surgeon")

        sidebar.set_vitals_model(model)
        screen.set_vitals_model(model)

        # Initial consistency
        assert sidebar._vital_val_labels["HR"].text() == screen.vitals_hud.val_hr.text() == "--"
        assert sidebar._vital_val_labels["SpO2"].text() == screen.vitals_hud.val_spo2.text() == "--"
        assert sidebar._vital_val_labels["BP"].text() == screen.vitals_hud.val_bp.text() == "--"
        assert sidebar._vital_val_labels["Temp"].text() == screen.vitals_hud.val_temp.text() == "--"

        # Update 1: HR = 74
        model.update_vitals({
            "heart_rate": 74.0,
            "spo2": 98.0,
            "blood_pressure": "118/74",
            "temperature": 36.8,
        })
        assert sidebar._vital_val_labels["HR"].text() == screen.vitals_hud.val_hr.text() == "74"
        assert sidebar._vital_val_labels["SpO2"].text() == screen.vitals_hud.val_spo2.text() == "98"
        assert sidebar._vital_val_labels["BP"].text() == screen.vitals_hud.val_bp.text() == "118/74"
        assert sidebar._vital_val_labels["Temp"].text() == screen.vitals_hud.val_temp.text() == "36.8"

        # Update 2: Dynamic generator change HR = 82
        model.update_vitals({
            "heart_rate": 82.0,
            "spo2": 97.0,
            "blood_pressure": "122/76",
            "temperature": 37.1,
        })
        assert sidebar._vital_val_labels["HR"].text() == screen.vitals_hud.val_hr.text() == "82"
        assert sidebar._vital_val_labels["SpO2"].text() == screen.vitals_hud.val_spo2.text() == "97"
        assert sidebar._vital_val_labels["BP"].text() == screen.vitals_hud.val_bp.text() == "122/76"
        assert sidebar._vital_val_labels["Temp"].text() == screen.vitals_hud.val_temp.text() == "37.1"

    def test_main_message_router_vitals_dispatch(self):
        """Verify main.py _on_message_received correctly routes 'patient_vitals' topic."""
        window = AetherConsole(username="test_surgeon", role="user")

        msg = {
            "topic": "patient_vitals",
            "source": "data_generator",
            "payload": {
                "heart_rate": 82.0,
                "spo2": 99.0,
                "blood_pressure": "124/80",
                "temperature": 36.9,
            }
        }
        window._on_message_received("patient_vitals", msg)

        # Both sidebar and live video HUD should reflect the routed packet
        assert window.sidebar._vital_val_labels["HR"].text() == "82"
        assert window.sidebar._vital_val_labels["SpO2"].text() == "99"
        assert window.sidebar._vital_val_labels["BP"].text() == "124/80"
        assert window.sidebar._vital_val_labels["Temp"].text() == "36.9"

        assert window.live_video.vitals_hud.val_hr.text() == "82"
        assert window.live_video.vitals_hud.val_spo2.text() == "99"
        assert window.live_video.vitals_hud.val_bp.text() == "124/80"
        assert window.live_video.vitals_hud.val_temp.text() == "36.9"

        # Disconnection dispatch
        window._on_broker_disconnected()
        assert window.sidebar._vital_val_labels["HR"].text() == "--"
        assert window.sidebar._vitals_status_label.text() == "DISCONNECTED"
        assert window.live_video.vitals_hud.val_hr.text() == "--"
        assert window.live_video.vitals_hud.status_badge.text() == "DISCONNECTED"

        window.close()

    def test_hud_resolution_scaling(self):
        """Verify HUD remains compact and correctly positioned at 1920x1080, 1600x900, 2560x1440."""
        screen = LiveVideoScreen(mode="surgeon")
        screen.show()
        hud = screen.vitals_hud

        for width, height in [(1600, 900), (1920, 1080), (2560, 1440)]:
            screen.resize(width, height)
            QApplication.processEvents()
            # Dimensions remain compact
            assert 260 <= hud.width() <= 300
            assert 55 <= hud.height() <= 70
            # Positioned at top-left inside video canvas container
            assert hud.x() == 16
            assert hud.y() == 16

        screen.close()

