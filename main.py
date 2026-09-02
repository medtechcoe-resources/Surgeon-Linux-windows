import sys
import os
import logging

log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QStackedWidget, QScrollArea)
from PyQt6.QtCore import Qt

from theme_manager import ThemeManager

from widgets.header import Header
from widgets.nav_tabs import NavBar
from widgets.patient_sidebar import PatientSidebar
from widgets.status_bar import StatusBar

from screens.preop_planning import PreopPlanningScreen
from screens.live_video import LiveVideoScreen
from screens.live_control import LiveControlScreen
from screens.settings import SettingsScreen
from screens.comm_center import CommCenterScreen

from models.patient_vitals_model import PatientVitalsModel

from shared_networking.connection_manager import ConnectionManager
from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager
from shared_networking.login_dialog import LoginDialog
from shared_networking.config import DATABASE_PATH


from shared_networking.video_stream import VideoReceiver


class AetherConsole(QMainWindow):
    def __init__(self, username: str = "", role: str = "user",
                 session_id: str = "", auth_manager: AuthManager = None):
        super().__init__()
        self._username = username
        self._role = role
        self._session_id = session_id
        self._auth_manager = auth_manager

        self.setWindowTitle("AETHER SURGICAL ROBOTIC CONSOLE — REV 4.2")
        self.resize(3440, 1440)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (contains E-Stop + theme toggle inline)
        self.header = Header()
        root.addWidget(self.header)

        # Nav tabs
        self.nav = NavBar()
        root.addWidget(self.nav)

        # Content area: left sidebar + stacked screens
        content_wrap = QWidget()
        content_h = QHBoxLayout(content_wrap)
        content_h.setContentsMargins(20, 0, 20, 0)
        content_h.setSpacing(16)

        self.sidebar = PatientSidebar()
        content_h.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.preop = PreopPlanningScreen()
        self.live_video = LiveVideoScreen(mode="surgeon")
        self.live_control = LiveControlScreen()
        self.settings = SettingsScreen(
            username=username, role=role, auth_manager=auth_manager)
        self.comm_center = CommCenterScreen()

        for screen in (self.preop, self.live_video, self.live_control,
                       self.settings, self.comm_center):
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setFrameShape(QScrollArea.Shape.NoFrame)
            scroller.setWidget(screen)
            self.stack.addWidget(scroller)

        content_h.addWidget(self.stack, 1)
        root.addWidget(content_wrap, 1)

        # Status bar
        self.status_bar_widget = StatusBar()
        root.addWidget(self.status_bar_widget)

        # Wire up navigation
        self.nav.tab_changed.connect(self.stack.setCurrentIndex)

        # Wire theme toggle (header.theme_btn is a _ThemeButton that repaints itself;
        # ThemeManager broadcasts the change to all subscribed widgets)
        self.header.theme_btn.clicked.connect(ThemeManager.instance().toggle)

        # ── Networking Setup ──────────────────────────────────────
        self._conn_manager = ConnectionManager(
            client_name="surgeon_console",
            publish_topics=[],
            subscribe_topics=[
                "patient_vitals",
                "robot_telemetry", "alerts",
                "connection_status", "system_status",
            ],
            username=username,
            role=role,
            session_id=session_id,
        )
        self._conn_manager.enable_auto_reconnect(True)

        # Dedicated TCP Video Receiver on Port 5001
        self.video_receiver = VideoReceiver(parent=self)
        self.video_receiver.frame_received.connect(self.live_video.update_frame)
        self.video_receiver.status_changed.connect(self._on_video_status_changed)
        self.video_receiver.start()

        # Wire Comm Center to connection manager
        self.comm_center.set_connection_manager(self._conn_manager)

        # Wire Settings to connection manager and auth
        self.settings.set_connection_manager(self._conn_manager)
        if auth_manager:
            self.settings.set_auth_context(auth_manager, username, role)

        # Wire Live Video to connection manager
        self.live_video.set_connection_manager(self._conn_manager)

        # Authoritative Patient Vitals Model (Single source of truth)
        self.patient_vitals_model = PatientVitalsModel(parent=self)
        self.sidebar.set_vitals_model(self.patient_vitals_model)
        self.live_video.set_vitals_model(self.patient_vitals_model)

        # Route messages to appropriate screens
        self._conn_manager.message_received.connect(self._on_message_received)
        self._conn_manager.disconnected.connect(self._on_broker_disconnected)

        # Auto-connect to broker on startup
        self._conn_manager.connect_to_broker()

    def _on_video_status_changed(self, status: str):
        if status == "Disconnected":
            self.live_video.on_stream_disconnected()

    def _on_broker_disconnected(self):
        """Handle broker disconnection by marking telemetry and vitals disconnected/stale in UI."""
        if hasattr(self, 'live_control'):
            self.live_control.set_telemetry_disconnected()
        if hasattr(self, 'patient_vitals_model'):
            self.patient_vitals_model.set_disconnected()

    def _on_message_received(self, topic: str, message: dict):
        """Route incoming pub-sub messages to registered UI screens."""
        if not isinstance(message, dict):
            return
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        if topic == "robot_telemetry":
            self.live_control.update_telemetry(payload)
        elif topic == "alerts":
            self.live_control.update_alerts(payload)
        elif topic == "patient_vitals":
            self.patient_vitals_model.update_vitals(payload)

    def closeEvent(self, event):
        """Clean up networking on window close."""
        if hasattr(self, 'video_receiver'):
            self.video_receiver.stop()
        self._conn_manager.cleanup()
        event.accept()


def main():
    app = QApplication(sys.argv)

    # Apply saved theme before any windows open
    tm = ThemeManager.instance()
    tm.apply_initial()

    # ── Initialize Security Database ─────────────────────────────
    db = AetherDatabase.instance()
    if not db.open(DATABASE_PATH):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Security Error",
            "Cannot open security database.\n"
            "Please run 'python broker.py --provision' first.",
        )
        sys.exit(1)

    # ── Initialize Authentication ─────────────────────────────
    auth = AuthManager()
    if not auth.is_provisioned():
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Not Provisioned",
            "No user accounts exist.\n"
            "Please run 'python broker.py --provision' first.",
        )
        sys.exit(1)

    # ── Show Login Dialog ─────────────────────────────────────
    login = LoginDialog(
        title="Surgeon Console — Login",
        auth_manager=auth,
    )
    if login.exec() != LoginDialog.DialogCode.Accepted:
        sys.exit(0)

    # ── Launch Main Window ────────────────────────────────────
    window = AetherConsole(
        username=login.username,
        role=login.role,
        session_id=login.session_id,
        auth_manager=auth,
    )
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
