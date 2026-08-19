# ═══════════════════════════════════════════════════════════════════
#  DATA GENERATOR — PUBLISHER
#  Wraps the shared ConnectionManager as a publish-only broker
#  client.  Wires the three generator signals to their respective
#  publish calls.
# ═══════════════════════════════════════════════════════════════════

import sys
import os
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

# Ensure project root on path so shared_networking is importable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shared_networking.connection_manager import ConnectionManager
from shared_networking.config import BROKER_HOST, BROKER_PORT

from generators import PatientVitalsGenerator, RobotTelemetryGenerator, AlertGenerator


class DataPublisher(QObject):
    """Connects to the broker and publishes all three data streams.

    Owns the three generator objects and wires their ``*_ready``
    signals to the appropriate ``publish()`` calls.

    Signals
    -------
    connected       Broker connection established.
    disconnected    Broker connection lost.
    error_occurred  Connection error string.
    log_message     (level, message) tuple for the console UI.
    stats_updated   Latest connection stats dict.
    published       (topic, payload) emitted each time a message is sent.
    """

    connected      = pyqtSignal()
    disconnected   = pyqtSignal()
    error_occurred = pyqtSignal(str)
    log_message    = pyqtSignal(str, str)   # (level, message)
    stats_updated  = pyqtSignal(dict)
    published      = pyqtSignal(str, dict)  # (topic, payload)

    PUBLISH_TOPICS = [
        "patient_vitals",
        "robot_telemetry",
        "alerts",
        "connection_status",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        # ── Connection manager ────────────────────────────────────
        self._conn = ConnectionManager(
            client_name="data_generator",
            publish_topics=self.PUBLISH_TOPICS,
            subscribe_topics=[],
            username="aether_datagen",
            role="data_generator",
            # session_id is a static placeholder. The data generator is a
            # service device authenticated via mTLS certificate (device_type=
            # 'data_generator'). The broker grants the 'data_generator' role
            # directly from the device type and does not validate this token.
            session_id="datagen-001",
            parent=self,
        )
        self._conn.enable_auto_reconnect(True)

        # Wire connection manager signals
        self._conn.connected.connect(self._on_connected)
        self._conn.disconnected.connect(self._on_disconnected)
        self._conn.error_occurred.connect(self.error_occurred)
        self._conn.log_message.connect(self.log_message)
        self._conn.stats_updated.connect(self.stats_updated)

        # ── Generators ────────────────────────────────────────────
        self.vitals_gen    = PatientVitalsGenerator(self)
        self.telemetry_gen = RobotTelemetryGenerator(self)
        self.alert_gen     = AlertGenerator(self)

        # Wire generator signals to publish methods
        self.vitals_gen.vitals_ready.connect(self._publish_vitals)
        self.telemetry_gen.telemetry_ready.connect(self._publish_telemetry)
        self.alert_gen.alert_ready.connect(self._publish_alert)

        self._paused = False

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._conn.is_connected

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def host(self) -> str:
        return self._conn.host

    @property
    def port(self) -> int:
        return self._conn.port

    # ── Public API ────────────────────────────────────────────────

    def start(self):
        """Connect to broker and start all generators."""
        self._conn.connect_to_broker()

    def stop(self):
        """Stop all generators and disconnect."""
        self.vitals_gen.stop()
        self.telemetry_gen.stop()
        self.alert_gen.stop()
        self._conn.cleanup()

    def pause_all(self):
        """Pause all generators (broker stays connected)."""
        if not self._paused:
            self._paused = True
            self.vitals_gen.pause()
            self.telemetry_gen.pause()
            self.alert_gen.pause()
            self.log_message.emit("WARNING", "All generators PAUSED")

    def resume_all(self):
        """Resume all generators."""
        if self._paused:
            self._paused = False
            self.vitals_gen.resume()
            self.telemetry_gen.resume()
            self.alert_gen.resume()
            self.log_message.emit("INFO", "All generators RESUMED")

    def get_stats(self) -> dict:
        """Return current connection statistics."""
        return self._conn.get_stats()

    # ── Connection handlers ────────────────────────────────────────

    def _on_connected(self):
        self.connected.emit()
        # Announce presence on the bus
        self._conn.publish("connection_status", {
            "event":       "data_generator_connected",
            "client_name": "data_generator",
            "timestamp":   datetime.now().isoformat(),
            "topics":      self.PUBLISH_TOPICS,
        })
        # Start generators
        self.vitals_gen.start()
        self.telemetry_gen.start()
        self.alert_gen.start()

    def _on_disconnected(self):
        self.vitals_gen.stop()
        self.telemetry_gen.stop()
        self.alert_gen.stop()
        self.disconnected.emit()

    # ── Publish helpers ───────────────────────────────────────────

    def _publish_vitals(self, payload: dict):
        if self._conn.is_connected:
            self._conn.publish("patient_vitals", payload)
            self.published.emit("patient_vitals", payload)

    def _publish_telemetry(self, payload: dict):
        if self._conn.is_connected:
            self._conn.publish("robot_telemetry", payload)
            self.published.emit("robot_telemetry", payload)

    def _publish_alert(self, alert: dict):
        if self._conn.is_connected:
            self._conn.publish("alerts", alert)
            self.published.emit("alerts", alert)
