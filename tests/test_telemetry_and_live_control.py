"""
Tests for telemetry routing and LiveControlScreen dynamic updates.
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

from screens.live_control import LiveControlScreen
from main import AetherConsole


class TestLiveControlTelemetry:
    def test_live_control_telemetry_update(self):
        screen = LiveControlScreen()

        # Telemetry packet format from Data Generator
        payload = {
            "timestamp": "2026-08-28T12:00:00.000",
            "robot_status": "ACTIVE",
            "motion_state": "MOVING",
            "servo_status": "NOMINAL",
            "torque_status": "NOMINAL",
            "end_effector_rotation": 1.25,
            "joint_angles": {
                "j1": 45.0, "j2": -30.0, "j3": 60.0,
                "j4": 15.0, "j5": 90.0, "j6": -10.0,
            },
            "tool_position": {
                "x": 150.25,
                "y": -45.50,
                "z": 280.75,
            },
            "velocity": 0.35,
            "force": 3.2,
            "cpu_usage": 24.5,
            "latency": 1.45,
        }

        screen.update_telemetry(payload)

        # Verify coordinates updated
        assert screen._coord_labels["X"].text() == "150.25"
        assert screen._coord_labels["Y"].text() == "-45.50"
        assert screen._coord_labels["Z"].text() == "280.75"
        assert screen._coord_labels["RX"].text() == "1.250"

        # Verify tip force updated
        assert screen._card_tip_force.value_label.text() == "3.2"

        # Verify robot simulator angles updated
        assert screen.robot._angles == [45.0, -30.0, 60.0]

        # Verify status indicators
        assert screen._live_label.text() == "LIVE"
        assert screen._health_labels["CPU"].text() == "24.5%"
        assert screen._health_labels["Latency"].text() == "1.45 ms"

    def test_live_control_alerts_update(self):
        screen = LiveControlScreen()
        alert = {
            "timestamp": "2026-08-28 12:05:00",
            "severity": "WARNING",
            "source": "Joint Controller",
            "message": "Joint Torque High",
        }
        screen.update_alerts(alert)
        assert screen._alerts_layout.count() > 0

    def test_live_control_disconnection(self):
        screen = LiveControlScreen()
        screen.set_telemetry_disconnected()
        assert screen._live_label.text() == "DISCONNECTED"

    def test_main_message_router_dispatch(self):
        window = AetherConsole(username="test_admin", role="admin")

        # Emit mock robot telemetry packet
        msg = {
            "topic": "robot_telemetry",
            "source": "data_generator",
            "payload": {
                "joint_angles": {"j1": 12.0, "j2": 34.0, "j3": 56.0},
                "tool_position": {"x": 100.0, "y": 200.0, "z": 300.0},
                "force": 1.8,
            }
        }
        window._on_message_received("robot_telemetry", msg)

        assert window.live_control._coord_labels["X"].text() == "100.00"
        assert window.live_control.robot._angles == [12.0, 34.0, 56.0]
        assert window.live_control._card_tip_force.value_label.text() == "1.8"

        window.close()
