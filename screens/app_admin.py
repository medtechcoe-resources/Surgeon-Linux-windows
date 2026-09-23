# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — APPLICATION ADMIN PANEL (Phase 4)
#
#  Standalone QMainWindow for the app_admin role.
#  Opened exclusively by admin_launcher.route_after_login() when
#  the authenticated role is 'app_admin'.
#
#  Tabs:
#    1. Hospitals     — list, create, activate/deactivate (with confirmation)
#    2. Users         — global user table with search
#    3. Create Hosp.  Admin — form to create hospital_admin accounts
#    4. Audit Log     — global read-only audit event table
#    5. Configuration — safe whitelist of non-sensitive app config
#
#  Security rules:
#    - All data operations go through AppAdminService (RBAC enforced).
#    - Session token is the sole source of actor identity.
#    - Configuration tab displays only safe, non-secret values.
#    - Hospital deactivation requires an explicit QMessageBox confirmation.
#    - Logout invalidates the session before closing.
#    - closeEvent also invalidates the session.
# ═══════════════════════════════════════════════════════════════════

import logging
import sys

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QFrame, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QDialog, QFormLayout, QComboBox,
    QTextEdit, QSizePolicy, QApplication,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

from theme_manager import ThemeManager
from shared_networking.authentication import AuthManager
from shared_networking.admin_service import AppAdminService

log = logging.getLogger(__name__)


# ─── Small UI helpers ─────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: #10B981; font-size: 11px; font-weight: 700; "
        "letter-spacing: 2px; font-family: 'Inter','Segoe UI',sans-serif;"
    )
    return lbl


def _make_table(headers: list) -> QTableWidget:
    tbl = QTableWidget(0, len(headers))
    tbl.setHorizontalHeaderLabels(headers)
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    tbl.setAlternatingRowColors(True)
    tbl.horizontalHeader().setStretchLastSection(True)
    tbl.verticalHeader().setVisible(False)
    tbl.setStyleSheet(
        "QTableWidget { background: #161B22; color: #E6EDF3; "
        "   gridline-color: #1C2333; border: 1px solid #1C2333; "
        "   font-family: 'Inter','Segoe UI',sans-serif; font-size:13px; }"
        "QTableWidget::item:selected { background: #1C4A8A; }"
        "QHeaderView::section { background: #0D2136; color: #6B8CB8; "
        "   font-size:11px; font-weight:700; letter-spacing:1px; "
        "   padding: 6px; border: none; }"
        "QTableWidget::item:alternate { background: #111820; }"
    )
    for i in range(len(headers) - 1):
        tbl.horizontalHeader().setSectionResizeMode(
            i, QHeaderView.ResizeMode.ResizeToContents)
    return tbl


def _action_btn(text: str, color: str = "#0095FF") -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(34)
    btn.setStyleSheet(
        f"QPushButton {{ background: {color}; color: white; border: none; "
        f"border-radius: 6px; font-size: 13px; font-weight: 700; "
        f"padding: 0 16px; font-family: 'Inter','Segoe UI',sans-serif; }}"
        f"QPushButton:hover {{ background: #1AA3FF; }}"
        f"QPushButton:disabled {{ background: #2D3748; color: #4A5568; }}"
    )
    return btn


def _field_input(placeholder: str = "", password: bool = False) -> QLineEdit:
    inp = QLineEdit()
    inp.setPlaceholderText(placeholder)
    if password:
        inp.setEchoMode(QLineEdit.EchoMode.Password)
    inp.setFixedHeight(36)
    inp.setStyleSheet(
        "QLineEdit { background: #161B22; color: #E6EDF3; "
        "border: 1px solid #2D3748; border-radius: 6px; "
        "padding: 0 12px; font-size: 13px; "
        "font-family: 'Inter','Segoe UI',sans-serif; }"
        "QLineEdit:focus { border: 1.5px solid #0095FF; }"
    )
    return inp


# ─── Create Hospital Dialog ───────────────────────────────────────

class _CreateHospitalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Hospital")
        self.setFixedSize(420, 280)
        self.setModal(True)
        self.setStyleSheet("background: #0D1117; color: #E6EDF3;")

        form = QFormLayout(self)
        form.setSpacing(14)
        form.setContentsMargins(24, 24, 24, 24)

        self._name = _field_input("e.g. Apollo Hospitals Chennai")
        self._code = _field_input("e.g. APOLLO (max 12 chars)")
        self._addr = _field_input("Optional address")

        form.addRow(QLabel("Hospital Name *"), self._name)
        form.addRow(QLabel("Hospital Code *"), self._code)
        form.addRow(QLabel("Address"), self._addr)

        btn_row = QHBoxLayout()
        self._ok_btn = _action_btn("Create")
        cancel = _action_btn("Cancel", "#374151")
        btn_row.addWidget(cancel)
        btn_row.addWidget(self._ok_btn)
        form.addRow(btn_row)

        self._ok_btn.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        for lbl in self.findChildren(QLabel):
            lbl.setStyleSheet(
                "color: #6B8CB8; font-size: 12px; "
                "font-family: 'Inter','Segoe UI',sans-serif;"
            )

    @property
    def name(self): return self._name.text().strip()
    @property
    def code(self): return self._code.text().strip()
    @property
    def address(self): return self._addr.text().strip() or None


# ─── Create Hospital Admin Dialog ────────────────────────────────

class _CreateHospAdminDialog(QDialog):
    def __init__(self, hospitals: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Hospital Admin")
        self.setFixedSize(480, 460)
        self.setModal(True)
        self.setStyleSheet("background: #0D1117; color: #E6EDF3;")

        form = QFormLayout(self)
        form.setSpacing(12)
        form.setContentsMargins(24, 24, 24, 24)

        self._hosp_combo = QComboBox()
        self._hosp_combo.setFixedHeight(36)
        self._hosp_combo.setStyleSheet(
            "QComboBox { background: #161B22; color: #E6EDF3; "
            "border: 1px solid #2D3748; border-radius: 6px; "
            "padding: 0 12px; font-size: 13px; }"
        )
        self._hospital_ids = []
        for h in hospitals:
            self._hosp_combo.addItem(f"{h['name']}  [{h['code']}]")
            self._hospital_ids.append(h["id"])

        self._username = _field_input("Username")
        self._password = _field_input("Password (min 8 chars)", password=True)
        self._confirm  = _field_input("Confirm password", password=True)
        self._fname    = _field_input("First name")
        self._lname    = _field_input("Last name")
        self._empid    = _field_input("Employee ID")

        form.addRow(QLabel("Hospital *"), self._hosp_combo)
        form.addRow(QLabel("Username *"), self._username)
        form.addRow(QLabel("Password *"), self._password)
        form.addRow(QLabel("Confirm *"),  self._confirm)
        form.addRow(QLabel("First Name *"), self._fname)
        form.addRow(QLabel("Last Name"),  self._lname)
        form.addRow(QLabel("Employee ID"), self._empid)

        btn_row = QHBoxLayout()
        self._ok_btn = _action_btn("Create")
        cancel = _action_btn("Cancel", "#374151")
        btn_row.addWidget(cancel)
        btn_row.addWidget(self._ok_btn)
        form.addRow(btn_row)

        self._ok_btn.clicked.connect(self._validate)
        cancel.clicked.connect(self.reject)

        for lbl in self.findChildren(QLabel):
            lbl.setStyleSheet(
                "color: #6B8CB8; font-size: 12px; "
                "font-family: 'Inter','Segoe UI',sans-serif;"
            )

    def _validate(self):
        if not self._username.text().strip():
            QMessageBox.warning(self, "Validation", "Username is required.")
            return
        if len(self._password.text()) < 8:
            QMessageBox.warning(self, "Validation",
                                "Password must be at least 8 characters.")
            return
        if self._password.text() != self._confirm.text():
            QMessageBox.warning(self, "Validation", "Passwords do not match.")
            return
        if not self._fname.text().strip():
            QMessageBox.warning(self, "Validation", "First name is required.")
            return
        self.accept()

    @property
    def hospital_id(self):
        idx = self._hosp_combo.currentIndex()
        return self._hospital_ids[idx] if self._hospital_ids else None

    @property
    def username(self): return self._username.text().strip()
    @property
    def password(self): return self._password.text()
    @property
    def first_name(self): return self._fname.text().strip()
    @property
    def last_name(self): return self._lname.text().strip()
    @property
    def employee_id(self): return self._empid.text().strip()


# ─── Main Admin Window ────────────────────────────────────────────

class AppAdminWindow(QMainWindow):
    """Standalone Application Admin Panel.

    Opened exclusively for role='app_admin'. All data operations are
    performed through AppAdminService, which enforces RBAC on every call.
    """

    def __init__(self, username: str, session_id: str,
                 auth_manager: AuthManager, parent=None):
        super().__init__(parent)
        self._username = username
        self._session_id = session_id
        self._auth = auth_manager
        self._svc = AppAdminService()
        self._logged_out = False

        self.setWindowTitle("AETHER — Application Admin Console")
        self.resize(1400, 900)

        self._build_ui()
        self._refresh_all()

        # Subscribe to theme changes
        ThemeManager.instance().theme_changed.connect(self._apply_base_style)
        self._apply_base_style(ThemeManager.instance().current)

    # ─── UI Construction ──────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = self._make_header()
        root.addWidget(header)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: #0A0E14; }"
            "QTabBar::tab { background: #111820; color: #6B8CB8; "
            "  padding: 10px 28px; font-size: 13px; font-weight: 600; "
            "  font-family: 'Inter','Segoe UI',sans-serif; "
            "  border-bottom: 2px solid transparent; }"
            "QTabBar::tab:selected { color: #E6EDF3; "
            "  border-bottom: 2px solid #10B981; background: #0A0E14; }"
            "QTabBar::tab:hover { color: #A0B4C8; background: #0D1117; }"
        )
        root.addWidget(self._tabs, 1)

        self._tabs.addTab(self._build_hospitals_tab(), "  Hospitals  ")
        self._tabs.addTab(self._build_users_tab(), "  Users  ")
        self._tabs.addTab(self._build_create_admin_tab(), "  Create Hospital Admin  ")
        self._tabs.addTab(self._build_audit_tab(), "  Audit Log  ")
        self._tabs.addTab(self._build_config_tab(), "  Configuration  ")

    def _make_header(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #060B11, stop:0.5 #0A1020, stop:1 #060B11);"
            "border-bottom: 1px solid #10B981;"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 0, 24, 0)

        badge = QLabel("⬡")
        badge.setStyleSheet("color: #10B981; font-size: 22px;")
        h.addWidget(badge)
        h.addSpacing(10)

        title = QLabel("AETHER  ·  APPLICATION ADMIN")
        title.setStyleSheet(
            "color: #E6EDF3; font-size: 15px; font-weight: 700; "
            "letter-spacing: 1px; font-family: 'Inter','Segoe UI',sans-serif;"
        )
        h.addWidget(title)
        h.addStretch()

        user_lbl = QLabel(f"Logged in as:  {self._username}")
        user_lbl.setStyleSheet(
            "color: #4A5568; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        h.addWidget(user_lbl)
        h.addSpacing(16)

        logout_btn = QPushButton("LOGOUT")
        logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_btn.setFixedHeight(32)
        logout_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #EF4444; "
            "border: 1px solid #EF4444; border-radius: 5px; "
            "font-size: 12px; font-weight: 700; letter-spacing: 1px; "
            "padding: 0 14px; font-family: 'Inter','Segoe UI',sans-serif; }"
            "QPushButton:hover { background: rgba(239,68,68,0.12); }"
        )
        logout_btn.clicked.connect(self._on_logout)
        h.addWidget(logout_btn)
        return bar

    # ── Tab: Hospitals ────────────────────────────────────────────

    def _build_hospitals_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("HOSPITAL REGISTRY"))
        top.addStretch()
        self._btn_create_hosp = _action_btn("＋ Create Hospital")
        self._btn_activate    = _action_btn("Activate",   "#10B981")
        self._btn_deactivate  = _action_btn("Deactivate", "#EF4444")
        self._btn_refresh_h   = _action_btn("↻ Refresh",  "#374151")
        for b in (self._btn_refresh_h, self._btn_activate,
                  self._btn_deactivate, self._btn_create_hosp):
            top.addWidget(b)
        v.addLayout(top)

        self._hosp_table = _make_table(
            ["Name", "Code", "Status", "Address", "Created"])
        v.addWidget(self._hosp_table, 1)

        self._btn_create_hosp.clicked.connect(self._on_create_hospital)
        self._btn_activate.clicked.connect(lambda: self._on_set_hospital_active(True))
        self._btn_deactivate.clicked.connect(lambda: self._on_set_hospital_active(False))
        self._btn_refresh_h.clicked.connect(self._refresh_hospitals)
        return w

    # ── Tab: Users ────────────────────────────────────────────────

    def _build_users_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("GLOBAL USER REGISTRY"))
        top.addStretch()
        self._user_search = _field_input("Search username, role, hospital…")
        self._user_search.setFixedWidth(300)
        self._user_search.textChanged.connect(self._filter_users)
        btn_refresh_u = _action_btn("↻ Refresh", "#374151")
        btn_refresh_u.clicked.connect(self._refresh_users)
        top.addWidget(self._user_search)
        top.addWidget(btn_refresh_u)
        v.addLayout(top)

        self._user_table = _make_table(
            ["Username", "User ID", "Role", "Scope", "Hospital", "Enabled", "Created"])
        v.addWidget(self._user_table, 1)
        return w

    # ── Tab: Create Hospital Admin ────────────────────────────────

    def _build_create_admin_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        v.addWidget(_section_label("CREATE HOSPITAL ADMINISTRATOR"))
        v.addSpacing(8)

        note = QLabel(
            "Hospital Admin creation is an Application Admin exclusive operation.\n"
            "The selected hospital is re-validated server-side — the UI selection "
            "is not itself authorization."
        )
        note.setStyleSheet(
            "color: #4A5568; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        note.setWordWrap(True)
        v.addWidget(note)
        v.addSpacing(12)

        form_frame = QFrame()
        form_frame.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        form = QFormLayout(form_frame)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)

        self._ca_hosp_combo = QComboBox()
        self._ca_hosp_combo.setFixedHeight(36)
        self._ca_hosp_combo.setStyleSheet(
            "QComboBox { background: #161B22; color: #E6EDF3; "
            "border: 1px solid #2D3748; border-radius: 6px; padding: 0 12px; "
            "font-size: 13px; font-family: 'Inter','Segoe UI',sans-serif; }"
        )
        self._ca_hospital_ids = []

        self._ca_username  = _field_input("Username")
        self._ca_password  = _field_input("Password (min 8 chars)", password=True)
        self._ca_confirm   = _field_input("Confirm password", password=True)
        self._ca_fname     = _field_input("First name")
        self._ca_lname     = _field_input("Last name")
        self._ca_empid     = _field_input("Employee ID")

        lbl_style = (
            "color: #6B8CB8; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        for lbl_text, widget in [
            ("Hospital *", self._ca_hosp_combo),
            ("Username *", self._ca_username),
            ("Password *", self._ca_password),
            ("Confirm *",  self._ca_confirm),
            ("First Name *", self._ca_fname),
            ("Last Name",  self._ca_lname),
            ("Employee ID", self._ca_empid),
        ]:
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(lbl_style)
            form.addRow(lbl, widget)

        self._ca_status = QLabel("")
        self._ca_status.setWordWrap(True)
        self._ca_status.setStyleSheet("color: #EF4444; font-size: 12px;")
        form.addRow(self._ca_status)

        self._ca_btn = _action_btn("Create Hospital Admin")
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._ca_btn)
        form.addRow(btn_row)

        v.addWidget(form_frame)
        v.addStretch()

        self._ca_btn.clicked.connect(self._on_create_hosp_admin_inline)
        return w

    # ── Tab: Audit Log ────────────────────────────────────────────

    def _build_audit_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("GLOBAL AUDIT LOG"))
        top.addStretch()
        btn_ref = _action_btn("↻ Refresh", "#374151")
        btn_ref.clicked.connect(self._refresh_audit)
        top.addWidget(btn_ref)
        v.addLayout(top)

        self._audit_table = _make_table(
            ["Timestamp", "Event", "Username", "Device", "Details", "IP"])
        v.addWidget(self._audit_table, 1)
        return w

    # ── Tab: Configuration ────────────────────────────────────────

    def _build_config_tab(self) -> QWidget:
        """Display safe, non-sensitive configuration values only.

        NEVER displays passwords, hashes, private keys, certificates
        containing sensitive private material, recovery secrets, or
        other authentication credentials.
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(16)

        v.addWidget(_section_label("APPLICATION CONFIGURATION"))

        warning = QLabel(
            "⚠  This tab displays safe configuration values only.  "
            "Passwords, private keys, certificates, and other credentials "
            "are never shown here."
        )
        warning.setStyleSheet("color: #F59E0B; font-size: 12px;")
        warning.setWordWrap(True)
        v.addWidget(warning)

        self._config_frame = QFrame()
        self._config_frame.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        config_layout = QFormLayout(self._config_frame)
        config_layout.setContentsMargins(24, 20, 24, 20)
        config_layout.setSpacing(14)

        lbl_style = (
            "color: #6B8CB8; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        val_style = (
            "color: #E6EDF3; font-size: 13px; font-weight: 600; "
            "font-family: 'JetBrains Mono','Consolas',monospace;"
        )

        self._cfg_labels = {}
        for key in ["version", "broker_host", "broker_port",
                    "database_file", "session_username", "session_role"]:
            lbl = QLabel(key.replace("_", " ").title())
            lbl.setStyleSheet(lbl_style)
            val = QLabel("—")
            val.setStyleSheet(val_style)
            config_layout.addRow(lbl, val)
            self._cfg_labels[key] = val

        v.addWidget(self._config_frame)
        v.addStretch()

        btn_refresh = _action_btn("↻ Refresh", "#374151")
        btn_refresh.clicked.connect(self._refresh_config)
        v.addWidget(btn_refresh)
        return w

    # ─── Data Refresh ─────────────────────────────────────────────

    def _refresh_all(self):
        self._refresh_hospitals()
        self._refresh_users()
        self._refresh_audit()
        self._refresh_config()
        self._refresh_hosp_combos()

    def _refresh_hospitals(self):
        ok, data = self._svc.list_hospitals(self._session_id)
        if not ok:
            return
        self._hosp_data = data
        self._hosp_table.setRowCount(0)
        for h in data:
            row = self._hosp_table.rowCount()
            self._hosp_table.insertRow(row)
            status = "Active" if h.get("is_active") else "Inactive"
            color  = "#10B981" if h.get("is_active") else "#EF4444"
            for col, val in enumerate([
                h.get("name", ""), h.get("code", ""),
                status, h.get("address", "") or "",
                (h.get("created_at", "") or "")[:10],
            ]):
                item = QTableWidgetItem(str(val))
                if col == 2:
                    item.setForeground(QColor(color))
                self._hosp_table.setItem(row, col, item)
        self._refresh_hosp_combos()

    def _refresh_hosp_combos(self):
        """Populate hospital dropdowns on the Create Hospital Admin tab."""
        ok, data = self._svc.list_hospitals(self._session_id)
        if not ok:
            return
        active = [h for h in data if h.get("is_active")]

        self._ca_hosp_combo.clear()
        self._ca_hospital_ids = []
        for h in active:
            self._ca_hosp_combo.addItem(f"{h['name']}  [{h['code']}]")
            self._ca_hospital_ids.append(h["id"])

    def _refresh_users(self):
        ok, data = self._svc.list_all_users(self._session_id)
        if not ok:
            return
        self._all_users = data
        self._populate_user_table(data)

    def _populate_user_table(self, users: list):
        self._user_table.setRowCount(0)
        for u in users:
            row = self._user_table.rowCount()
            self._user_table.insertRow(row)
            enabled = "Yes" if u.get("is_enabled") else "No"
            for col, val in enumerate([
                u.get("username", ""),
                u.get("user_id", "") or "",
                u.get("role", ""),
                u.get("scope", ""),
                u.get("hospital_name", "") or "—",
                enabled,
                (u.get("created_at", "") or "")[:10],
            ]):
                item = QTableWidgetItem(str(val))
                if col == 5:
                    item.setForeground(
                        QColor("#10B981") if val == "Yes" else QColor("#EF4444"))
                self._user_table.setItem(row, col, item)

    def _filter_users(self, text: str):
        if not hasattr(self, "_all_users"):
            return
        text = text.lower()
        filtered = [
            u for u in self._all_users
            if text in (u.get("username") or "").lower()
            or text in (u.get("role") or "").lower()
            or text in (u.get("hospital_name") or "").lower()
            or text in (u.get("user_id") or "").lower()
        ]
        self._populate_user_table(filtered)

    def _refresh_audit(self):
        ok, data = self._svc.get_audit_logs(self._session_id, limit=200)
        if not ok:
            return
        self._audit_table.setRowCount(0)
        for entry in data:
            row = self._audit_table.rowCount()
            self._audit_table.insertRow(row)
            for col, val in enumerate([
                (entry.get("timestamp") or "")[:19],
                entry.get("event_type", ""),
                entry.get("username", "") or "",
                entry.get("device_id", "") or "",
                entry.get("details", "") or "",
                entry.get("ip_address", "") or "",
            ]):
                self._audit_table.setItem(row, col, QTableWidgetItem(str(val)))

    def _refresh_config(self):
        ok, cfg = self._svc.get_app_config(self._session_id)
        if not ok:
            return
        for key, lbl in self._cfg_labels.items():
            lbl.setText(str(cfg.get(key, "—")))

    # ─── Actions ──────────────────────────────────────────────────

    def _on_create_hospital(self):
        dlg = _CreateHospitalDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ok, result = self._svc.create_hospital(
            self._session_id, dlg.name, dlg.code, dlg.address)
        if ok:
            QMessageBox.information(self, "Success",
                                    f"Hospital created.\nID: {result}")
            self._refresh_hospitals()
        else:
            QMessageBox.warning(self, "Error", result)

    def _on_set_hospital_active(self, active: bool):
        row = self._hosp_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select Hospital",
                                    "Please select a hospital first.")
            return
        if not hasattr(self, "_hosp_data") or row >= len(self._hosp_data):
            return
        hosp = self._hosp_data[row]
        action = "Activate" if active else "Deactivate"

        # Explicit confirmation required before deactivation (R5)
        reply = QMessageBox.warning(
            self, f"Confirm {action}",
            f"Are you sure you want to {action.lower()} hospital:\n\n"
            f"  {hosp['name']}  [{hosp['code']}]\n\n"
            "This action will take effect immediately.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, err = self._svc.set_hospital_active(
            self._session_id, hosp["id"], active)
        if ok:
            self._refresh_hospitals()
        else:
            QMessageBox.warning(self, "Error", err)

    def _on_create_hosp_admin_inline(self):
        self._ca_status.setText("")
        idx = self._ca_hosp_combo.currentIndex()
        if idx < 0 or not self._ca_hospital_ids:
            self._ca_status.setText("No hospitals available. Create one first.")
            return

        hospital_id = self._ca_hospital_ids[idx]
        username    = self._ca_username.text().strip()
        password    = self._ca_password.text()
        confirm     = self._ca_confirm.text()
        fname       = self._ca_fname.text().strip()
        lname       = self._ca_lname.text().strip()
        empid       = self._ca_empid.text().strip()

        if not username:
            self._ca_status.setText("Username is required.")
            return
        if len(password) < 8:
            self._ca_status.setText("Password must be at least 8 characters.")
            return
        if password != confirm:
            self._ca_status.setText("Passwords do not match.")
            return
        if not fname:
            self._ca_status.setText("First name is required.")
            return

        ok, result = self._svc.create_hospital_admin(
            self._session_id, hospital_id, username, password,
            fname, lname, empid)
        if ok:
            self._ca_status.setStyleSheet("color: #10B981; font-size: 12px;")
            self._ca_status.setText(
                f"Hospital Admin created. User ID: {result}")
            for f in (self._ca_username, self._ca_password,
                      self._ca_confirm, self._ca_fname,
                      self._ca_lname, self._ca_empid):
                f.clear()
            self._refresh_users()
        else:
            self._ca_status.setStyleSheet("color: #EF4444; font-size: 12px;")
            self._ca_status.setText(f"Error: {result}")

    # ─── Logout / Close ───────────────────────────────────────────

    def _on_logout(self):
        reply = QMessageBox.question(
            self, "Logout",
            "Are you sure you want to logout?\n"
            "Your session will be invalidated.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_logout()

    def _do_logout(self):
        if not self._logged_out:
            self._logged_out = True
            log.info(f"[APP_ADMIN] Logout: invalidating session for {self._username}")
            self._auth.remove_session(self._session_id)
        self.close()

    def closeEvent(self, event):
        """Invalidate the session on any close path — normal or forced."""
        if not self._logged_out:
            self._logged_out = True
            log.info(
                f"[APP_ADMIN] Window closed: invalidating session for {self._username}"
            )
            self._auth.remove_session(self._session_id)
        event.accept()

    def _apply_base_style(self, theme: str):
        if theme == "dark":
            self.setStyleSheet(
                "QMainWindow, QWidget { background: #0A0E14; color: #E6EDF3; }"
                "QLabel { color: #E6EDF3; font-family: 'Inter','Segoe UI',sans-serif; }"
            )
        else:
            self.setStyleSheet(
                "QMainWindow, QWidget { background: #F1F5F9; color: #0F172A; }"
                "QLabel { color: #0F172A; font-family: 'Inter','Segoe UI',sans-serif; }"
            )
