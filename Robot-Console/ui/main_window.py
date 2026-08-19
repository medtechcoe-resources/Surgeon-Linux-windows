# ═══════════════════════════════════════════════════════════════════
#  ROBOT CONSOLE — MAIN WINDOW
#  The central QMainWindow that assembles the top bar, tab widget,
#  and wires all services together.
#
#  Data (telemetry, vitals, alerts) is now produced by the separate
#  Data Generator backend and received via the pub-sub bridge.
# ═══════════════════════════════════════════════════════════════════

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTabWidget, QApplication,
)

from constants import C, WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_TITLE
from styles import generate_stylesheet
from networking.tcp_client import TCPClient
from networking.protocol import MSG_ROBOT_TELEMETRY, MSG_VITALS_DATA, MSG_ALERT
from services.connection_monitor import ConnectionMonitor
from services.pubsub_bridge import PubSubBridge
from ui.tab_dashboard import DashboardTab
from ui.tab_patient_vitals import PatientVitalsTab
from ui.tab_robot_telemetry import RobotTelemetryTab
from ui.tab_communication import CommunicationTab
from ui.tab_alerts import AlertsTab
from ui.widgets import StatusBadge, StatusIndicator
from screens.live_video import LiveVideoScreen


class MainWindow(QMainWindow):
    """Robot Console main window — assembles the top bar, 5 tabs,
    and connects all background services.

    Telemetry, patient vitals, and alerts are received from the
    Data Generator backend via the pub-sub broker rather than
    being generated locally.
    """

    def __init__(self, username: str = "", role: str = "user",
                 session_id: str = ""):
        super().__init__()
        self._username = username
        self._role = role
        self._session_id = session_id

        # Rolling alert list for badge counting
        self._alerts: list[dict] = []

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(1280, 720)

        # Apply global stylesheet
        self.setStyleSheet(generate_stylesheet())

        # ── Initialise Services ───────────────────────────────────
        self._tcp_client  = TCPClient(self)
        self._conn_monitor = ConnectionMonitor(self)
        self._conn_monitor.set_client(self._tcp_client)

        # Pub-Sub Bridge — subscribes to telemetry, vitals, alerts
        self._pubsub = PubSubBridge(
            self, username=username, role=role, session_id=session_id)

        # ── Build UI ──────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        self._main_layout = QVBoxLayout(central)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self._build_topbar()
        self._build_tabs()

        # ── Wire Signals ──────────────────────────────────────────
        self._connect_signals()

        # ── Start Services ────────────────────────────────────────
        self._start_services()

        # ── Clock ─────────────────────────────────────────────────
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

    # ══════════════════════════════════════════════════════════════
    #  TOP BAR (scaled for 3440×1440)
    # ══════════════════════════════════════════════════════════════

    def _build_topbar(self):
        """Build the top bar matching the Surgeon Console design."""
        topbar = QWidget()
        topbar.setFixedHeight(72)
        topbar.setStyleSheet(f"background-color: {C['bg2']};")

        tb_layout = QHBoxLayout(topbar)
        tb_layout.setContentsMargins(24, 0, 24, 0)
        tb_layout.setSpacing(0)

        # Title
        title = QLabel("  ROBOT CONSOLE")
        title.setFont(QFont("Consolas", 30, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C['txt0']};")
        tb_layout.addWidget(title)

        version = QLabel("  MEDROBOT OS v4.2")
        version.setFont(QFont("Consolas", 18))
        version.setStyleSheet(f"color: {C['txt2']};")
        tb_layout.addWidget(version)

        tb_layout.addStretch()

        # Connection status indicator
        self._topbar_conn_indicator = StatusIndicator(C["red"], size=14)
        tb_layout.addWidget(self._topbar_conn_indicator)
        tb_layout.addSpacing(6)

        self._topbar_conn_label = QLabel("DISCONNECTED")
        self._topbar_conn_label.setFont(
            QFont("Consolas", 18, QFont.Weight.Bold))
        self._topbar_conn_label.setStyleSheet(f"color: {C['red']};")
        tb_layout.addWidget(self._topbar_conn_label)
        tb_layout.addSpacing(20)

        # Robot status badge
        self._topbar_robot_badge = StatusBadge("IDLE", C["txt2"])
        tb_layout.addWidget(self._topbar_robot_badge)
        tb_layout.addSpacing(20)

        # Alert badge
        self._topbar_alert_badge = StatusBadge("0", C["txt2"])
        self._topbar_alert_badge.setVisible(False)
        tb_layout.addWidget(self._topbar_alert_badge)
        tb_layout.addSpacing(20)

        # Data Generator status
        self._topbar_datagen_label = QLabel("DATA GEN: WAITING")
        self._topbar_datagen_label.setFont(QFont("Consolas", 14))
        self._topbar_datagen_label.setStyleSheet(f"color: {C['amber']};")
        tb_layout.addWidget(self._topbar_datagen_label)
        tb_layout.addSpacing(20)

        # Clock
        clock_frame = QWidget()
        clock_frame.setStyleSheet(
            f"background-color: {C['cyan']}; padding: 6px 14px;")
        cf_layout = QHBoxLayout(clock_frame)
        cf_layout.setContentsMargins(14, 6, 14, 6)

        self._clock_label = QLabel("--:--:--")
        self._clock_label.setFont(QFont("Consolas", 24, QFont.Weight.Bold))
        self._clock_label.setStyleSheet("color: white;")
        cf_layout.addWidget(self._clock_label)

        tb_layout.addWidget(clock_frame)

        self._main_layout.addWidget(topbar)

        # Cyan accent line (matches Surgeon Console)
        accent = QFrame()
        accent.setFixedHeight(3)
        accent.setStyleSheet(f"background-color: {C['cyan']};")
        self._main_layout.addWidget(accent)

        border = QFrame()
        border.setFixedHeight(1)
        border.setStyleSheet(f"background-color: {C['border']};")
        self._main_layout.addWidget(border)

    # ══════════════════════════════════════════════════════════════
    #  TABS
    # ══════════════════════════════════════════════════════════════

    def _build_tabs(self):
        """Build the 5 main tabs."""
        self._tab_widget = QTabWidget()
        self._tab_widget.setDocumentMode(True)

        # Create tab instances
        self._live_video_tab = LiveVideoScreen(mode="robot")
        self._dashboard_tab  = DashboardTab()
        self._vitals_tab     = PatientVitalsTab()
        self._telemetry_tab  = RobotTelemetryTab()
        self._comm_tab       = CommunicationTab()
        self._alerts_tab     = AlertsTab()

        # Wire comm tab buttons
        self._comm_tab.on_connect    = self._on_connect
        self._comm_tab.on_disconnect = self._on_disconnect

        # Add tabs
        self._tab_widget.addTab(self._live_video_tab, "  LIVE VIDEO  ")
        self._tab_widget.addTab(self._dashboard_tab,  "  DASHBOARD  ")
        self._tab_widget.addTab(self._vitals_tab,     "  PATIENT VITALS  ")
        self._tab_widget.addTab(self._telemetry_tab,  "  ROBOT TELEMETRY  ")
        self._tab_widget.addTab(self._comm_tab,       "  COMMUNICATION CENTER  ")
        self._tab_widget.addTab(self._alerts_tab,     "  ALERTS  ")

        self._main_layout.addWidget(self._tab_widget)

    # ══════════════════════════════════════════════════════════════
    #  SIGNAL WIRING
    # ══════════════════════════════════════════════════════════════

    def _connect_signals(self):
        """Connect all service signals to UI update slots."""

        # TCP Client signals (legacy — kept for direct connection mode)
        self._tcp_client.connected.connect(self._on_connected)
        self._tcp_client.disconnected.connect(self._on_disconnected)
        self._tcp_client.data_received.connect(self._on_data_received)
        self._tcp_client.data_sent.connect(self._on_data_sent)
        self._tcp_client.error_occurred.connect(self._on_tcp_error)
        self._tcp_client.log_message.connect(self._on_log_message)
        self._tcp_client.stats_updated.connect(self._on_stats_updated)

        # Pub-Sub Bridge signals — data arrives from Data Generator
        self._pubsub.connected.connect(self._on_pubsub_connected)
        self._pubsub.disconnected.connect(self._on_pubsub_disconnected)
        self._pubsub.vitals_received.connect(self._on_pubsub_vitals)
        self._pubsub.telemetry_received.connect(self._on_pubsub_telemetry)
        self._pubsub.alert_received.connect(self._on_pubsub_alert)
        self._pubsub.error_occurred.connect(self._on_tcp_error)
        self._pubsub.log_message.connect(self._on_log_message)
        self._pubsub.stats_updated.connect(self._on_pubsub_stats)
        self._pubsub.data_received.connect(self._on_data_received)
        self._pubsub.raw_data_sent.connect(self._on_raw_data_sent)
        self._pubsub.raw_data_received.connect(self._on_raw_data_received)

        # Connection monitor signals
        self._conn_monitor.stats_updated.connect(self._on_stats_updated)

    # ══════════════════════════════════════════════════════════════
    #  SERVICE MANAGEMENT
    # ══════════════════════════════════════════════════════════════

    def _start_services(self):
        """Start background services."""
        self._conn_monitor.start()
        # Auto-connect to pub-sub broker to receive data from Data Generator
        self._pubsub.start()
        # Inject the pub-sub manager into LiveVideoScreen for broadcasting
        self._live_video_tab.set_connection_manager(self._pubsub)

    def _stop_services(self):
        """Stop all background services."""
        self._conn_monitor.stop()
        self._tcp_client.disconnect_from_server()
        self._pubsub.stop()

    # ══════════════════════════════════════════════════════════════
    #  CONNECTION HANDLERS
    # ══════════════════════════════════════════════════════════════

    def _on_connect(self, host: str, port: int):
        """Handle connect button click from Communication tab."""
        self._pubsub.connect_to_server(host, port)

    def _on_disconnect(self):
        """Handle disconnect button click."""
        self._pubsub.disconnect_from_server()

    def _on_connected(self):
        """TCP connection established."""
        if self._tcp_client.is_connected:
            self._comm_tab.set_connected(True)
            self._topbar_conn_indicator.set_color(C["green"])
            self._topbar_conn_label.setText("CONNECTED")
            self._topbar_conn_label.setStyleSheet(f"color: {C['green']};")
            self._topbar_robot_badge.set_text_and_color("ACTIVE", C["green"])

    def _on_disconnected(self):
        """TCP connection lost."""
        if not self._pubsub.is_connected:
            self._comm_tab.set_connected(False)
            self._topbar_conn_indicator.set_color(C["red"])
            self._topbar_conn_label.setText("DISCONNECTED")
            self._topbar_conn_label.setStyleSheet(f"color: {C['red']};")

    # ── Pub-Sub Connection Handlers ───────────────────────────────

    def _on_pubsub_connected(self):
        """Pub-sub broker connection established."""
        self._comm_tab.set_connected(True)
        self._topbar_conn_indicator.set_color(C["green"])
        self._topbar_conn_label.setText("BROKER CONNECTED")
        self._topbar_conn_label.setStyleSheet(f"color: {C['green']};")
        self._topbar_robot_badge.set_text_and_color("RECEIVING", C["cyan"])
        self._topbar_datagen_label.setText("DATA GEN: CONNECTED")
        self._topbar_datagen_label.setStyleSheet(f"color: {C['green']};")

    def _on_pubsub_disconnected(self):
        """Pub-sub broker connection lost."""
        self._comm_tab.set_connected(False)
        self._topbar_conn_indicator.set_color(C["red"])
        self._topbar_conn_label.setText("DISCONNECTED")
        self._topbar_conn_label.setStyleSheet(f"color: {C['red']};")
        self._topbar_robot_badge.set_text_and_color("IDLE", C["txt2"])
        self._topbar_datagen_label.setText("DATA GEN: OFFLINE")
        self._topbar_datagen_label.setStyleSheet(f"color: {C['red']};")

    def _on_pubsub_vitals(self, vitals_msg: dict):
        """Handle patient vitals received via pub-sub from Data Generator."""
        self._vitals_tab.update_vitals(vitals_msg)

    def _on_pubsub_telemetry(self, payload: dict):
        """Handle robot telemetry received via pub-sub from Data Generator."""
        # Build a minimal history dict for sparklines (empty — broker doesn't send it)
        self._telemetry_tab.update_telemetry_from_dict(payload)
        # Update dashboard robot status
        self._topbar_robot_badge.set_text_and_color(
            payload.get("motion_state", "ACTIVE"), C["green"])

    def _on_pubsub_alert(self, alert: dict):
        """Handle alert received via pub-sub from Data Generator."""
        from models.data_models import AlertEntry
        entry = AlertEntry(
            timestamp=alert.get("timestamp", ""),
            severity=alert.get("severity", "INFO"),
            source=alert.get("source", ""),
            message=alert.get("message", ""),
        )
        self._alerts_tab.add_alert(entry)

        # Update rolling list for badge
        self._alerts.insert(0, alert)
        if len(self._alerts) > 200:
            self._alerts = self._alerts[:200]

        # Update topbar badge
        total    = len(self._alerts)
        critical = sum(1 for a in self._alerts if a.get("severity") == "CRITICAL")
        if critical > 0:
            self._topbar_alert_badge.set_text_and_color(f" {total} ", C["red"])
        elif total > 0:
            self._topbar_alert_badge.set_text_and_color(f" {total} ", C["amber"])
        self._topbar_alert_badge.setVisible(total > 0)

    def _on_pubsub_stats(self, stats: dict):
        """Handle pub-sub stats update."""
        self._dashboard_tab.update_connection_stats(stats)
        self._comm_tab.update_stats(stats)

    # ══════════════════════════════════════════════════════════════
    #  DATA HANDLERS
    # ══════════════════════════════════════════════════════════════

    def _on_data_received(self, message: dict):
        """Handle processed data for UI updates."""
        msg_type = message.get("type", "")
        if msg_type == MSG_VITALS_DATA:
            self._vitals_tab.update_vitals(message)

    def _on_raw_data_received(self, message: dict, plaintext: bytes, encrypted: bytes):
        """Handle raw data received from the broker."""
        msg_type = message.get("topic", "")
        if not msg_type:
            msg_type = message.get("type", "UNKNOWN")
        self._comm_tab.update_raw_received(message, plaintext, encrypted)
        self._comm_tab.add_log_entry("INFO", f"Received {msg_type} packet")

    def _on_raw_data_sent(self, message: dict, plaintext: bytes, encrypted: bytes):
        """Handle raw data sent to the broker."""
        msg_type = message.get("topic", "")
        if not msg_type:
            msg_type = message.get("type", "UNKNOWN")
        self._comm_tab.update_raw_sent(message, plaintext, encrypted)
        self._comm_tab.add_log_entry("INFO", f"Sent {msg_type} packet")

    def _on_data_sent(self, message: dict):
        """Legacy handler for TCP client."""
        msg_type = message.get("type", "")
        self._comm_tab.update_sent_json(message)
        self._comm_tab.add_log_entry("INFO", f"Sent {msg_type} packet")

    def _on_tcp_error(self, error: str):
        """Handle TCP error."""
        self._comm_tab.add_log_entry("ERROR", error)

    def _on_log_message(self, level: str, message: str):
        """Handle log message from TCP client."""
        self._comm_tab.add_log_entry(level, message)

    def _on_stats_updated(self, stats: dict):
        """Handle updated connection statistics."""
        if self._tcp_client.is_connected:
            self._dashboard_tab.update_connection_stats(stats)
            self._comm_tab.update_stats(stats)

    # ══════════════════════════════════════════════════════════════
    #  CLOCK
    # ══════════════════════════════════════════════════════════════

    def _update_clock(self):
        """Update the topbar clock."""
        self._clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    # ══════════════════════════════════════════════════════════════
    #  CLEANUP
    # ══════════════════════════════════════════════════════════════

    def closeEvent(self, event):
        """Clean up services on window close."""
        self._stop_services()
        event.accept()
