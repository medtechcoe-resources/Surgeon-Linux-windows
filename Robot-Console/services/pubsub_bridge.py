# ═══════════════════════════════════════════════════════════════════
#  ROBOT CONSOLE — PUB-SUB BRIDGE SERVICE
#  Connects to the shared pub-sub broker.
#
#  Publishes:   connection_status
#  Subscribes:  robot_telemetry, patient_vitals, alerts
#
#  Data is now produced by the standalone Data Generator backend.
#  This bridge receives all three streams and emits Qt signals for
#  the UI tabs to consume without modification.
# ═══════════════════════════════════════════════════════════════════

import sys
import os
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

# Ensure the project root is on sys.path for the shared networking package
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from shared_networking.connection_manager import ConnectionManager
from shared_networking.config import BROKER_HOST, BROKER_PORT


class PubSubBridge(QObject):
    """Bridge between the Robot Console UI and the pub-sub broker.

    Publishes:
        - connection_status (on connect/disconnect)

    Subscribes:
        - robot_telemetry   (produced by Data Generator)
        - patient_vitals    (produced by Data Generator)
        - alerts            (produced by Data Generator)

    Emits Qt signals for received data so existing UI tabs can
    consume it without modification.
    """

    # ── Signals for UI tabs ────────────────────────────────────────
    vitals_received    = pyqtSignal(dict)   # patient vitals data
    telemetry_received = pyqtSignal(dict)   # robot telemetry data
    alert_received     = pyqtSignal(dict)   # alert entry
    connected          = pyqtSignal()
    disconnected       = pyqtSignal()
    error_occurred     = pyqtSignal(str)
    log_message        = pyqtSignal(str, str)   # (level, message)
    stats_updated      = pyqtSignal(dict)
    data_received      = pyqtSignal(dict)       # raw message for comm tab
    raw_data_sent      = pyqtSignal(dict, bytes, bytes)
    raw_data_received  = pyqtSignal(dict, bytes, bytes)

    def __init__(self, parent=None, username: str = "", role: str = "",
                 session_id: str = ""):
        super().__init__(parent)

        self._conn_manager = ConnectionManager(
            client_name="robot_console",
            publish_topics=["connection_status"],
            subscribe_topics=["robot_telemetry", "patient_vitals", "alerts"],
            username=username,
            role=role,
            session_id=session_id,
            parent=self,
        )
        self._conn_manager.enable_auto_reconnect(True)

        # Wire connection manager signals
        self._conn_manager.connected.connect(self._on_connected)
        self._conn_manager.disconnected.connect(self._on_disconnected)
        self._conn_manager.error_occurred.connect(self.error_occurred)
        self._conn_manager.log_message.connect(self.log_message)
        self._conn_manager.stats_updated.connect(self.stats_updated)
        self._conn_manager.message_received.connect(self._on_message)
        self._conn_manager.raw_data_sent.connect(self.raw_data_sent)
        self._conn_manager.raw_data_received.connect(self.raw_data_received)

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._conn_manager.is_connected

    @property
    def host(self) -> str:
        return self._conn_manager.host

    @property
    def port(self) -> int:
        return self._conn_manager.port

    # ── Public API ────────────────────────────────────────────────

    def start(self):
        """Connect to broker."""
        self._conn_manager.connect_to_broker()

    def stop(self):
        """Disconnect from broker."""
        self._conn_manager.cleanup()

    def connect_to_server(self, host: str = None, port: int = None):
        """Connect to broker (compatible with comm tab interface)."""
        self._conn_manager.connect_to_broker(host, port)

    def disconnect_from_server(self):
        """Disconnect from broker (compatible with comm tab interface)."""
        self._conn_manager.disconnect_from_broker()

    def reconnect(self):
        """Reconnect to broker."""
        self.disconnect_from_server()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self.connect_to_server())

    def get_stats(self) -> dict:
        """Return current connection statistics."""
        return self._conn_manager.get_stats()

    # ── Signal Handlers ───────────────────────────────────────────

    def _on_connected(self):
        self.connected.emit()
        # Announce presence on the bus
        self._conn_manager.publish("connection_status", {
            "event":       "robot_console_connected",
            "client_name": "robot_console",
            "timestamp":   datetime.now().isoformat(),
        })

    def _on_disconnected(self):
        self.disconnected.emit()

    def _on_message(self, topic: str, message: dict):
        """Route incoming pub-sub messages to the appropriate handler."""
        payload = message.get("payload", {})

        if topic == "robot_telemetry":
            self._handle_telemetry(message, payload)

        elif topic == "patient_vitals":
            self._handle_vitals(message, payload)

        elif topic == "alerts":
            self._handle_alert(message, payload)

    # ── Per-topic handlers ────────────────────────────────────────

    def _handle_telemetry(self, message: dict, payload: dict):
        """Forward robot telemetry to the telemetry tab."""
        self.telemetry_received.emit(payload)
        self.data_received.emit({
            "type":      "ROBOT_TELEMETRY",
            "timestamp": message.get("timestamp", ""),
            "payload":   payload,
        })

    def _handle_vitals(self, message: dict, payload: dict):
        """Convert vitals payload and forward to patient vitals tab."""
        vitals_msg = {
            "type":      "VITALS_DATA",
            "timestamp": message.get("timestamp", ""),
            "payload": {
                "hr":     payload.get("heart_rate", 0),
                "spo2":   payload.get("spo2", 0),
                "nibp_s": self._parse_bp(payload.get("blood_pressure", "0/0"), 0),
                "nibp_d": self._parse_bp(payload.get("blood_pressure", "0/0"), 1),
                "etco2":  payload.get("etco2", 38.0),
                "rr":     payload.get("respiration", 0),
                "temp":   payload.get("temperature", 0),
                "ecg_status": payload.get("ecg_status", "---"),
            },
        }
        self.vitals_received.emit(vitals_msg)
        self.data_received.emit(vitals_msg)

    def _handle_alert(self, message: dict, payload: dict):
        """Forward alert to the alerts tab."""
        self.alert_received.emit(payload)
        self.data_received.emit({
            "type":      "ALERT",
            "timestamp": message.get("timestamp", ""),
            "payload":   payload,
        })

    @staticmethod
    def _parse_bp(bp_str: str, index: int) -> float:
        """Parse blood pressure string 'sys/dia' into components."""
        try:
            parts = str(bp_str).split("/")
            return float(parts[index])
        except (IndexError, ValueError):
            return 0.0
