"""
Settings tab — Centralized configuration & control hub.

Six sections in a 2-column layout:
  Column 1: General, Communication, Security
  Column 2: Robot, Diagnostics, Logs

Role-aware: admin-only controls hidden/disabled for 'user' role.
Wired to ConnectionManager for live stats and AuthManager for password changes.
"""
from datetime import datetime

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QFrame, QPushButton, QComboBox,
                             QScrollArea, QLineEdit, QTextEdit)
from PyQt6.QtCore import Qt, QTimer
from widgets.card import PanelFrame
from theme_manager import ThemeManager
from shared_networking.config import APP_VERSION, SYSTEM_NAME


def _field_row(label_text: str, value_widget, label_width=180):
    """Create a horizontal label-value row."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 6, 0, 6)
    lbl = QLabel(label_text)
    lbl.setObjectName("FieldLabel")
    lbl.setFixedWidth(label_width)
    row.addWidget(lbl)
    if isinstance(value_widget, QHBoxLayout) or isinstance(value_widget, QVBoxLayout):
        row.addLayout(value_widget)
    else:
        row.addWidget(value_widget)
    row.addStretch()
    return row


def _status_indicator(color: str = "#EF4444", text: str = "DISCONNECTED"):
    """Create a dot + label status indicator."""
    row = QHBoxLayout()
    row.setSpacing(8)
    dot = QFrame()
    dot.setFixedSize(12, 12)
    dot.setStyleSheet(f"background-color: {color}; border-radius: 6px;")
    row.addWidget(dot)
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 14px;")
    row.addWidget(lbl)
    row.addStretch()
    return row, dot, lbl


def _value_label(text: str = "---", color: str = "#E6EDF3", mono=False):
    """Create a styled value label."""
    lbl = QLabel(text)
    font_family = "'JetBrains Mono', 'Consolas', monospace" if mono else "'Inter', 'Segoe UI', sans-serif"
    lbl.setStyleSheet(
        f"color: {color}; font-size: 14px; font-weight: 600; "
        f"font-family: {font_family};"
    )
    return lbl


def _action_button(text: str, style: str = "SecondaryButton"):
    """Create a styled action button."""
    btn = QPushButton(text)
    btn.setProperty("class", style)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(36)
    return btn


class SettingsScreen(QWidget):
    """Centralized Settings screen with 6 sections."""

    def __init__(self, parent=None, conn_manager=None, auth_manager=None,
                 username: str = "", role: str = "user"):
        super().__init__(parent)
        self._conn_manager = conn_manager
        self._auth_manager = auth_manager
        self._username = username
        self._role = role
        self._log_entries = []  # Structured log buffer

        self._build_ui()

        # Stats refresh timer
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_diagnostics)
        self._stats_timer.start()

    def set_connection_manager(self, conn_mgr):
        """Attach the shared ConnectionManager instance (post-init wiring)."""
        self._conn_manager = conn_mgr
        conn_mgr.connected.connect(self._on_connected)
        conn_mgr.disconnected.connect(self._on_disconnected)
        conn_mgr.log_message.connect(self._on_log_message)
        conn_mgr.stats_updated.connect(self._on_stats_updated)

    def set_auth_context(self, auth_manager, username: str, role: str):
        """Set the authentication context (post-init wiring)."""
        self._auth_manager = auth_manager
        self._username = username
        self._role = role
        self._apply_role_restrictions()

    # ══════════════════════════════════════════════════════════════
    #  UI BUILD
    # ══════════════════════════════════════════════════════════════

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        outer = QHBoxLayout(container)
        outer.setContentsMargins(0, 14, 0, 14)
        outer.setSpacing(16)

        # ═══ Column 1: General + Communication + Security ═══
        col1 = QVBoxLayout()
        col1.setSpacing(14)
        col1.addWidget(self._build_general_section())
        col1.addWidget(self._build_communication_section())
        col1.addWidget(self._build_security_section())
        col1.addStretch()
        outer.addLayout(col1, 1)

        # ═══ Column 2: Robot + Diagnostics + Logs ═══
        col2 = QVBoxLayout()
        col2.setSpacing(14)
        col2.addWidget(self._build_robot_section())
        col2.addWidget(self._build_diagnostics_section())
        col2.addWidget(self._build_logs_section(), 1)
        col2.addStretch()
        outer.addLayout(col2, 1)

        scroll.setWidget(container)
        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.addWidget(scroll)

    # ── General Section ───────────────────────────────────────────

    def _build_general_section(self) -> QWidget:
        panel = PanelFrame("General")

        # System Name
        self._sys_name_lbl = _value_label(SYSTEM_NAME)
        panel.add_layout(_field_row("System Name", self._sys_name_lbl))

        # Theme
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Dark (Default)", "Light"])
        self._theme_combo.setFixedWidth(200)
        self._theme_combo.setStyleSheet(
            "background-color:#161B22; color:#E6EDF3; "
            "border:1px solid #2D3748; padding:6px; border-radius:4px;"
        )
        tm = ThemeManager.instance()
        self._theme_combo.setCurrentIndex(0 if tm.is_dark() else 1)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        panel.add_layout(_field_row("Theme", self._theme_combo))

        # Version
        self._version_lbl = _value_label(f"v{APP_VERSION}", color="#6B7B8D")
        panel.add_layout(_field_row("Application Version", self._version_lbl))

        panel.add_stretch()
        return panel

    # ── Communication Section ─────────────────────────────────────

    def _build_communication_section(self) -> QWidget:
        panel = PanelFrame("Communication")

        # Broker IP
        self._broker_ip_input = QLineEdit("127.0.0.1")
        self._broker_ip_input.setFixedWidth(200)
        self._broker_ip_input.setStyleSheet(
            "background-color:#161B22; color:#E6EDF3; "
            "border:1px solid #2D3748; padding:8px; border-radius:6px; font-size:14px;"
        )
        panel.add_layout(_field_row("Broker IP", self._broker_ip_input))

        # Broker Port
        self._broker_port_input = QLineEdit("5000")
        self._broker_port_input.setFixedWidth(100)
        self._broker_port_input.setStyleSheet(
            "background-color:#161B22; color:#E6EDF3; "
            "border:1px solid #2D3748; padding:8px; border-radius:6px; font-size:14px;"
        )
        panel.add_layout(_field_row("Broker Port", self._broker_port_input))

        # Connection Status
        self._comm_status_row, self._comm_dot, self._comm_status_lbl = \
            _status_indicator("#EF4444", "DISCONNECTED")
        panel.add_layout(_field_row("Connection Status", self._comm_status_row))

        # Client ID
        self._client_id_lbl = _value_label("---", mono=True)
        panel.add_layout(_field_row("Client ID", self._client_id_lbl))

        # Connected Time
        self._connected_time_lbl = _value_label("---", color="#6B7B8D", mono=True)
        panel.add_layout(_field_row("Connected Time", self._connected_time_lbl))

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._reconnect_btn = _action_button("Reconnect", "SecondaryButton")
        self._reconnect_btn.clicked.connect(self._on_reconnect)
        btn_row.addWidget(self._reconnect_btn)

        self._disconnect_btn = _action_button("Disconnect", "DangerButton")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self._disconnect_btn)

        self._restart_btn = _action_button("Restart Communication", "PrimaryButton")
        self._restart_btn.clicked.connect(self._on_restart_comm)
        btn_row.addWidget(self._restart_btn)

        btn_row.addStretch()
        panel.add_layout(btn_row)

        panel.add_stretch()
        return panel

    # ── Security Section ──────────────────────────────────────────

    def _build_security_section(self) -> QWidget:
        panel = PanelFrame("Security")

        # Encryption Status
        self._enc_status_row, self._enc_dot, self._enc_status_lbl = \
            _status_indicator("#10B981", "ENABLED")
        panel.add_layout(_field_row("Encryption Status", self._enc_status_row))

        # Algorithm
        self._enc_algo_lbl = _value_label(
            "mTLS (TLS 1.3 / AES-256-GCM)", color="#6B7B8D")
        panel.add_layout(_field_row("Algorithm", self._enc_algo_lbl))

        # Current User
        self._current_user_lbl = _value_label(
            self._username or "---", color="#0095FF", mono=True)
        panel.add_layout(_field_row("Current User", self._current_user_lbl))

        # Role
        role_color = "#10B981" if self._role == "admin" else "#6B7B8D"
        self._role_lbl = _value_label(
            (self._role or "---").upper(), color=role_color, mono=True)
        panel.add_layout(_field_row("User Role", self._role_lbl))

        # ── Change Password (Admin only) ──
        self._pw_section = QWidget()
        pw_layout = QVBoxLayout(self._pw_section)
        pw_layout.setContentsMargins(0, 8, 0, 0)
        pw_layout.setSpacing(8)

        pw_header = QLabel("CHANGE ADMIN PASSWORD")
        pw_header.setStyleSheet(
            "color: #6B7B8D; font-size: 11px; font-weight: 700; "
            "letter-spacing: 1.5px; font-family: 'JetBrains Mono', monospace;"
        )
        pw_layout.addWidget(pw_header)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #1C2333;")
        pw_layout.addWidget(sep)

        input_style = (
            "background-color:#161B22; color:#E6EDF3; "
            "border:1px solid #2D3748; padding:8px; "
            "border-radius:6px; font-size:13px;"
        )

        self._current_pw = QLineEdit()
        self._current_pw.setPlaceholderText("Current password")
        self._current_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._current_pw.setFixedWidth(260)
        self._current_pw.setStyleSheet(input_style)
        pw_layout.addLayout(_field_row("Current Password", self._current_pw))

        self._new_pw = QLineEdit()
        self._new_pw.setPlaceholderText("New password (min 6 chars)")
        self._new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_pw.setFixedWidth(260)
        self._new_pw.setStyleSheet(input_style)
        pw_layout.addLayout(_field_row("New Password", self._new_pw))

        self._confirm_pw = QLineEdit()
        self._confirm_pw.setPlaceholderText("Confirm new password")
        self._confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm_pw.setFixedWidth(260)
        self._confirm_pw.setStyleSheet(input_style)
        pw_layout.addLayout(_field_row("Confirm Password", self._confirm_pw))

        self._pw_status_lbl = QLabel("")
        self._pw_status_lbl.setStyleSheet("font-size: 12px;")
        self._pw_status_lbl.setWordWrap(True)
        pw_layout.addWidget(self._pw_status_lbl)

        self._save_pw_btn = _action_button("Save Password", "PrimaryButton")
        self._save_pw_btn.setFixedWidth(160)
        self._save_pw_btn.clicked.connect(self._on_change_password)
        pw_layout.addWidget(self._save_pw_btn)

        panel.add_widget(self._pw_section)

        # Hide password section for non-admin users
        if self._role != "admin":
            self._pw_section.setVisible(False)

        panel.add_stretch()
        return panel

    # ── Robot Section ─────────────────────────────────────────────

    def _build_robot_section(self) -> QWidget:
        panel = PanelFrame("Robot")

        # Robot connection info (from received telemetry)
        self._robot_ip_lbl = _value_label("127.0.0.1", mono=True)
        panel.add_layout(_field_row("Robot IP", self._robot_ip_lbl))

        self._robot_broker_lbl = _value_label("127.0.0.1:5000", mono=True)
        panel.add_layout(_field_row("Broker Address", self._robot_broker_lbl))

        # Robot connection status
        self._robot_status_row, self._robot_dot, self._robot_status_lbl = \
            _status_indicator("#EF4444", "DISCONNECTED")
        panel.add_layout(_field_row("Connection Status", self._robot_status_row))

        # Heartbeat
        self._robot_heartbeat_lbl = _value_label("---", color="#6B7B8D", mono=True)
        panel.add_layout(_field_row("Heartbeat", self._robot_heartbeat_lbl))

        # Latency
        self._robot_latency_lbl = _value_label("--- ms", color="#10B981", mono=True)
        panel.add_layout(_field_row("Latency", self._robot_latency_lbl))

        # Robot buttons
        robot_btn_row = QHBoxLayout()
        robot_btn_row.setSpacing(8)

        self._robot_restart_btn = _action_button(
            "Restart Communication", "SecondaryButton")
        self._robot_restart_btn.clicked.connect(self._on_restart_comm)
        robot_btn_row.addWidget(self._robot_restart_btn)

        self._robot_connect_btn = _action_button("Connect", "PrimaryButton")
        self._robot_connect_btn.clicked.connect(self._on_reconnect)
        robot_btn_row.addWidget(self._robot_connect_btn)

        self._robot_disconnect_btn = _action_button("Disconnect", "DangerButton")
        self._robot_disconnect_btn.clicked.connect(self._on_disconnect)
        robot_btn_row.addWidget(self._robot_disconnect_btn)

        robot_btn_row.addStretch()
        panel.add_layout(robot_btn_row)

        panel.add_stretch()
        return panel

    # ── Diagnostics Section ───────────────────────────────────────

    def _build_diagnostics_section(self) -> QWidget:
        panel = PanelFrame("Diagnostics")

        # Connection Status
        self._diag_conn_row, self._diag_conn_dot, self._diag_conn_lbl = \
            _status_indicator("#EF4444", "DISCONNECTED")
        panel.add_layout(_field_row("Connection Status", self._diag_conn_row))

        # Network Latency
        self._diag_latency_lbl = _value_label("--- ms", color="#10B981", mono=True)
        panel.add_layout(_field_row("Network Latency", self._diag_latency_lbl))

        # Broker Status
        self._diag_broker_row, self._diag_broker_dot, self._diag_broker_lbl = \
            _status_indicator("#EF4444", "UNKNOWN")
        panel.add_layout(_field_row("Broker Status", self._diag_broker_row))

        # Message counters
        self._diag_sent_lbl = _value_label("0", mono=True)
        panel.add_layout(_field_row("Sent Messages", self._diag_sent_lbl))

        self._diag_recv_lbl = _value_label("0", mono=True)
        panel.add_layout(_field_row("Received Messages", self._diag_recv_lbl))

        self._diag_enc_err_lbl = _value_label("0", color="#EF4444", mono=True)
        panel.add_layout(_field_row("Encryption Errors", self._diag_enc_err_lbl))

        self._diag_conn_err_lbl = _value_label("0", color="#EF4444", mono=True)
        panel.add_layout(_field_row("Connection Errors", self._diag_conn_err_lbl))

        panel.add_stretch()
        return panel

    # ── Logs Section ──────────────────────────────────────────────

    def _build_logs_section(self) -> QWidget:
        panel = PanelFrame("Logs")

        # Category filter buttons
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self._active_filter = "ALL"
        filter_categories = ["ALL", "COMM", "AUTH", "SYSTEM", "BROKER"]
        self._filter_btns = {}

        for cat in filter_categories:
            btn = QPushButton(cat)
            btn.setFixedHeight(28)
            btn.setFixedWidth(70)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=cat: self._on_filter(c))
            filter_row.addWidget(btn)
            self._filter_btns[cat] = btn

        filter_row.addStretch()
        panel.add_layout(filter_row)
        self._update_filter_styles()

        # Log viewer
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(
            "background-color: #0D1117; color: #B5BEC8; "
            "border: 1px solid #1C2333; border-radius: 8px; "
            "padding: 10px; font-size: 11px; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace;"
        )
        self._log_text.setPlaceholderText("Log entries will appear here...")
        panel.add_widget(self._log_text)

        # Action buttons
        log_btn_row = QHBoxLayout()
        log_btn_row.setSpacing(8)

        self._export_btn = _action_button("Export Logs", "SecondaryButton")
        self._export_btn.clicked.connect(self._on_export_logs)
        log_btn_row.addWidget(self._export_btn)

        self._clear_btn = _action_button("Clear Logs", "DangerButton")
        self._clear_btn.clicked.connect(self._on_clear_logs)
        log_btn_row.addWidget(self._clear_btn)

        # Clear button admin-only
        if self._role != "admin":
            self._clear_btn.setEnabled(False)
            self._clear_btn.setToolTip("Admin access required")

        log_btn_row.addStretch()
        panel.add_layout(log_btn_row)

        return panel

    # ══════════════════════════════════════════════════════════════
    #  EVENT HANDLERS
    # ══════════════════════════════════════════════════════════════

    def _on_theme_changed(self, index: int):
        tm = ThemeManager.instance()
        if (index == 0 and not tm.is_dark()) or \
           (index == 1 and tm.is_dark()):
            tm.toggle()

    def _on_reconnect(self):
        if self._conn_manager:
            host = self._broker_ip_input.text().strip()
            port = int(self._broker_port_input.text().strip() or "5000")
            self._conn_manager.enable_auto_reconnect(True)
            self._conn_manager.connect_to_broker(host, port)

    def _on_disconnect(self):
        if self._conn_manager:
            self._conn_manager.disconnect_from_broker()

    def _on_restart_comm(self):
        if self._conn_manager:
            self._conn_manager.restart_connection()

    def _on_change_password(self):
        if not self._auth_manager:
            self._pw_status_lbl.setText("Authentication not available.")
            self._pw_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px;")
            return

        current = self._current_pw.text()
        new_pw = self._new_pw.text()
        confirm = self._confirm_pw.text()

        if not current or not new_pw or not confirm:
            self._pw_status_lbl.setText("All fields are required.")
            self._pw_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px;")
            return

        if new_pw != confirm:
            self._pw_status_lbl.setText("New passwords do not match.")
            self._pw_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px;")
            return

        success, msg = self._auth_manager.change_password(
            self._username, current, new_pw, self._role)

        if success:
            self._pw_status_lbl.setText(msg)
            self._pw_status_lbl.setStyleSheet("color: #10B981; font-size: 12px;")
            self._current_pw.clear()
            self._new_pw.clear()
            self._confirm_pw.clear()
            self.add_log("AUTH", "INFO", "Admin password changed successfully")
        else:
            self._pw_status_lbl.setText(msg)
            self._pw_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px;")

    def _on_filter(self, category: str):
        self._active_filter = category
        self._update_filter_styles()
        self._refresh_log_display()

    def _on_export_logs(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Logs", "aether_logs.txt",
            "Text Files (*.txt);;All Files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    for entry in self._log_entries:
                        f.write(
                            f"{entry['timestamp']} [{entry['category']}] "
                            f"{entry['level']}: {entry['message']}\n"
                        )
                self.add_log("SYSTEM", "INFO", f"Logs exported to {path}")
            except Exception as e:
                self.add_log("SYSTEM", "ERROR", f"Failed to export logs: {e}")

    def _on_clear_logs(self):
        if self._role != "admin":
            return
        self._log_entries.clear()
        self._log_text.clear()
        self.add_log("SYSTEM", "INFO", "Logs cleared by admin")

    # ══════════════════════════════════════════════════════════════
    #  CONNECTION SIGNAL HANDLERS
    # ══════════════════════════════════════════════════════════════

    def _on_connected(self):
        self._set_status_indicator(
            self._comm_dot, self._comm_status_lbl, "#10B981", "CONNECTED")
        self._set_status_indicator(
            self._diag_conn_dot, self._diag_conn_lbl, "#10B981", "CONNECTED")
        self._set_status_indicator(
            self._diag_broker_dot, self._diag_broker_lbl, "#10B981", "CONNECTED")
        self._set_status_indicator(
            self._robot_dot, self._robot_status_lbl, "#10B981", "CONNECTED")
        self.add_log("COMM", "INFO", "Connected to broker")

    def _on_disconnected(self):
        self._set_status_indicator(
            self._comm_dot, self._comm_status_lbl, "#EF4444", "DISCONNECTED")
        self._set_status_indicator(
            self._diag_conn_dot, self._diag_conn_lbl, "#EF4444", "DISCONNECTED")
        self._set_status_indicator(
            self._diag_broker_dot, self._diag_broker_lbl, "#EF4444", "DISCONNECTED")
        self._set_status_indicator(
            self._robot_dot, self._robot_status_lbl, "#EF4444", "DISCONNECTED")
        self.add_log("COMM", "WARNING", "Disconnected from broker")

    def _on_log_message(self, level: str, message: str):
        self.add_log("COMM", level, message)

    def _on_stats_updated(self, stats: dict):
        # Client ID
        self._client_id_lbl.setText(stats.get("client_name", "---"))

        # Connected time
        self._connected_time_lbl.setText(stats.get("uptime", "---"))

        # Broker address
        remote = stats.get("remote_address", "---")
        self._robot_broker_lbl.setText(remote)

        # Diagnostics
        self._diag_sent_lbl.setText(str(stats.get("packets_sent", 0)))
        self._diag_recv_lbl.setText(str(stats.get("packets_received", 0)))
        self._diag_conn_err_lbl.setText(str(stats.get("errors", 0)))

        # Encryption errors
        enc_errors = stats.get("encryption_errors", 0) + \
                     stats.get("decryption_errors", 0)
        self._diag_enc_err_lbl.setText(str(enc_errors))

        # Encryption status
        if stats.get("encryption_enabled", False):
            self._set_status_indicator(
                self._enc_dot, self._enc_status_lbl, "#10B981", "ENABLED")
            self._enc_algo_lbl.setText(
                stats.get("encryption_algorithm", "---"))
        else:
            self._set_status_indicator(
                self._enc_dot, self._enc_status_lbl, "#F59E0B", "DISABLED")

        # Robot heartbeat
        last_recv = stats.get("last_received_time", "---")
        self._robot_heartbeat_lbl.setText(last_recv)

        # Auth info
        self._current_user_lbl.setText(
            stats.get("username", self._username) or "---")
        role = stats.get("role", self._role) or "---"
        role_color = "#10B981" if role == "admin" else "#6B7B8D"
        self._role_lbl.setText(role.upper())
        self._role_lbl.setStyleSheet(
            f"color: {role_color}; font-size: 14px; font-weight: 600; "
            f"font-family: 'JetBrains Mono', monospace;"
        )

    # ══════════════════════════════════════════════════════════════
    #  PUBLIC LOGGING API
    # ══════════════════════════════════════════════════════════════

    def add_log(self, category: str, level: str, message: str):
        """Add a structured log entry."""
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "category": category.upper(),
            "level": level.upper(),
            "message": message,
        }
        self._log_entries.append(entry)

        # Keep max 1000 entries
        if len(self._log_entries) > 1000:
            self._log_entries = self._log_entries[-800:]

        # Append to display if matches filter
        if self._active_filter == "ALL" or \
           entry["category"] == self._active_filter:
            self._append_log_html(entry)

    # ══════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════

    def _apply_role_restrictions(self):
        """Apply role-based visibility/enabled state."""
        is_admin = self._role == "admin"

        # Password section
        self._pw_section.setVisible(is_admin)

        # Clear logs
        self._clear_btn.setEnabled(is_admin)
        if not is_admin:
            self._clear_btn.setToolTip("Admin access required")

        # Communication editing
        self._broker_ip_input.setReadOnly(not is_admin)
        self._broker_port_input.setReadOnly(not is_admin)

        # Update display
        self._current_user_lbl.setText(self._username or "---")
        role_color = "#10B981" if is_admin else "#6B7B8D"
        self._role_lbl.setText(self._role.upper())
        self._role_lbl.setStyleSheet(
            f"color: {role_color}; font-size: 14px; font-weight: 600; "
            f"font-family: 'JetBrains Mono', monospace;"
        )

    def _refresh_diagnostics(self):
        """Periodic refresh of diagnostic values."""
        if self._conn_manager:
            stats = self._conn_manager.get_stats()
            self._on_stats_updated(stats)

    def _set_status_indicator(self, dot: QFrame, label: QLabel,
                              color: str, text: str):
        dot.setStyleSheet(
            f"background-color: {color}; border-radius: 6px;")
        label.setText(text)
        label.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 14px;")

    def _update_filter_styles(self):
        for cat, btn in self._filter_btns.items():
            if cat == self._active_filter:
                btn.setStyleSheet(
                    "background-color: #0095FF; color: white; "
                    "border: none; border-radius: 4px; "
                    "font-size: 11px; font-weight: 700; "
                    "font-family: 'JetBrains Mono', monospace;"
                )
            else:
                btn.setStyleSheet(
                    "background-color: #161B22; color: #6B7B8D; "
                    "border: 1px solid #2D3748; border-radius: 4px; "
                    "font-size: 11px; font-weight: 600; "
                    "font-family: 'JetBrains Mono', monospace;"
                )

    def _refresh_log_display(self):
        """Re-render log display with current filter."""
        self._log_text.clear()
        for entry in self._log_entries:
            if self._active_filter == "ALL" or \
               entry["category"] == self._active_filter:
                self._append_log_html(entry)

    def _append_log_html(self, entry: dict):
        """Append a single log entry as styled HTML."""
        level_colors = {
            "INFO": "#0095FF",
            "WARNING": "#F59E0B",
            "ERROR": "#EF4444",
            "DEBUG": "#6B7B8D",
        }
        cat_colors = {
            "COMM": "#00D4AA",
            "AUTH": "#8B5CF6",
            "SYSTEM": "#F59E0B",
            "BROKER": "#0095FF",
        }
        level_color = level_colors.get(entry["level"], "#B5BEC8")
        cat_color = cat_colors.get(entry["category"], "#6B7B8D")

        self._log_text.append(
            f'<span style="color:#4A5568;">{entry["timestamp"]}</span> '
            f'<span style="color:{cat_color};font-weight:bold;">'
            f'[{entry["category"]}]</span> '
            f'<span style="color:{level_color};font-weight:bold;">'
            f'{entry["level"]}</span> '
            f'<span style="color:#B5BEC8;">{entry["message"]}</span>'
        )

        # Auto-trim
        if self._log_text.document().blockCount() > 500:
            cursor = self._log_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down,
                                cursor.MoveMode.KeepAnchor, 100)
            cursor.removeSelectedText()
