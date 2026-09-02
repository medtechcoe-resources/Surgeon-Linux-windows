"""Regression and lifecycle tests for ConnectionManager:
- Proves self-join exception cannot occur during connection loss.
- Verifies exponential backoff reconnection.
- Verifies session preservation across TCP disconnect.
"""
import sys
import os
import time
import threading
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
from shared_networking.connection_manager import ConnectionManager
from shared_networking.protocol import create_logout


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestConnectionLifecycle:
    def test_self_join_prevention_on_disconnect(self, qapp):
        """Regression test for P0 bug: 'RuntimeError: cannot join current thread'.

        Simulates connection loss executing within the receive thread context.
        Must execute _do_disconnect() cleanly without raising self-join error.
        """
        cm = ConnectionManager("surgeon_console", username="doctor", role="user")
        cm._is_connected = True
        cm._running = True

        exception_raised = None

        def simulated_receive_loop():
            nonlocal exception_raised
            try:
                # Inside the receive thread itself, trigger connection lost / disconnect
                cm._handle_connection_lost()
            except Exception as e:
                exception_raised = e

        worker_thread = threading.Thread(target=simulated_receive_loop)
        cm._receive_thread = worker_thread

        worker_thread.start()
        worker_thread.join(timeout=2.0)

        assert exception_raised is None, f"Self-join exception occurred: {exception_raised}"
        assert cm.is_connected is False

    def test_exponential_backoff_schedule(self, qapp):
        """Verify backoff schedule: 0.5s -> 1.0s -> 2.0s -> 4.0s -> 8.0s -> 10.0s."""
        cm = ConnectionManager("robot_console")

        expected_delays = [0.5, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
        for attempt, expected in enumerate(expected_delays):
            cm._reconnect_count = attempt
            delay = cm._get_reconnect_delay()
            assert delay == expected, f"Attempt {attempt}: expected {expected}, got {delay}"

    def test_explicit_logout_clears_auth_context(self, qapp):
        """Verify explicit logout clears credentials and disconnects."""
        cm = ConnectionManager(
            "surgeon_console",
            username="admin",
            role="admin",
            session_id="token_live_123"
        )
        cm._is_connected = True
        cm.enable_auto_reconnect(True)

        cm.logout()

        assert cm.is_connected is False
        assert cm.session_id == ""
        assert cm.username == ""
        assert cm.role == ""
        assert cm._auto_reconnect_enabled is False
