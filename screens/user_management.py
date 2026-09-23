# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — USER MANAGEMENT (Phase 4 Refactor)
#
#  Unified User Management component embedded as a top-level tab
#  in the main AetherConsole shell.
#
#  Role-aware presentation:
#    - app_admin:       Full multi-hospital management, global users,
#                       create hospital admins, global audit log, config.
#    - hospital_admin:  Hospital-scoped overview, hospital users,
#                       create staff (tech/surgeon/doctor/observer),
#                       departments, scoped audit log.
#                       NO hospital selector — hospital derived strictly
#                       from authenticated session.
#
#  Security rules:
#    - UI visibility is NEVER the security boundary.
#    - All data operations go through AppAdminService or HospitalAdminService.
#    - Server-side AuthorizationService enforces RBAC and hospital isolation.
#    - Role changes and deactivations require explicit confirmation.
# ═══════════════════════════════════════════════════════════════════

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QFrame, QPushButton, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QFormLayout, QComboBox, QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from theme_manager import ThemeManager
from shared_networking.authentication import AuthManager
from shared_networking.admin_service import (
    AppAdminService,
    HospitalAdminService,
    HOSPITAL_ADMIN_ALLOWED_ROLES,
)

log = logging.getLogger(__name__)

_ALLOWED_ROLES_LIST = sorted(HOSPITAL_ADMIN_ALLOWED_ROLES)


# ─── UI Helpers ───────────────────────────────────────────────────

def _section_label(text: str, color: str = "#10B981") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 11px; font-weight: 700; "
        f"letter-spacing: 2px; font-family: 'Inter','Segoe UI',sans-serif;"
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
        f"QPushButton:hover {{ opacity: 0.9; }}"
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


def _info_row(label: str, value_label: QLabel, form: QFormLayout):
    lbl = QLabel(label)
    lbl.setStyleSheet(
        "color: #4A7BB8; font-size: 12px; "
        "font-family: 'Inter','Segoe UI',sans-serif;"
    )
    value_label.setStyleSheet(
        "color: #E6EDF3; font-size: 13px; font-weight: 600; "
        "font-family: 'Inter','Segoe UI',sans-serif;"
    )
    form.addRow(lbl, value_label)


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
        self._ok_btn = _action_btn("Create", "#10B981")
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
        if not self.name:
            QMessageBox.warning(self, "Validation", "Hospital Name is required.")
            return
        if not self.code:
            QMessageBox.warning(self, "Validation", "Hospital Code is required.")
            return
        self.accept()

    @property
    def name(self): return self._name.text().strip()
    @property
    def code(self): return self._code.text().strip()
    @property
    def address(self): return self._addr.text().strip() or None


# ─── App Admin View ───────────────────────────────────────────────

class AppAdminView(QWidget):
    """Internal App Admin panel with 5 sections:

    1. Hospitals: List, create, activate/deactivate
    2. Users: Global user table with text search
    3. Create Hospital Admin: Form to create hospital_admin accounts
    4. Audit Log: Global read-only audit events
    5. Configuration: Whitelist of non-sensitive configuration values
    """

    def __init__(self, session_id: str, auth_manager: Optional[AuthManager] = None, parent=None):
        super().__init__(parent)
        self._session_id = session_id
        self._auth = auth_manager
        db = auth_manager.db if auth_manager else None
        self._svc = AppAdminService(db=db)

        self._build_ui()
        self._refresh_all()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

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

        self._tabs.addTab(self._build_hospitals_tab(), "Hospitals")
        self._tabs.addTab(self._build_users_tab(), "Users")
        self._tabs.addTab(self._build_create_admin_tab(), "Create Hospital Admin")
        self._tabs.addTab(self._build_audit_tab(), "Audit Log")
        self._tabs.addTab(self._build_config_tab(), "Configuration")

    # ── Tab: Hospitals ────────────────────────────────────────────

    def _build_hospitals_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("HOSPITAL REGISTRY", "#10B981"))
        top.addStretch()
        self._btn_create_hosp = _action_btn("＋ Create Hospital", "#10B981")
        self._btn_activate    = _action_btn("Activate",   "#0095FF")
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
        top.addWidget(_section_label("GLOBAL USER REGISTRY", "#10B981"))
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
        v.addWidget(_section_label("CREATE HOSPITAL ADMINISTRATOR", "#10B981"))
        v.addSpacing(6)

        note = QLabel(
            "Hospital Admin creation is an Application Admin exclusive operation.\n"
            "The selected hospital is re-validated server-side — client parameters are never trusted."
        )
        note.setStyleSheet("color: #4A5568; font-size: 12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addSpacing(8)

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

        lbl_style = "color: #6B8CB8; font-size: 12px;"
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

        self._ca_btn = _action_btn("Create Hospital Admin", "#10B981")
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._ca_btn)
        form.addRow(btn_row)

        v.addWidget(form_frame)
        v.addStretch()

        self._ca_btn.clicked.connect(self._on_create_hosp_admin)
        return w

    # ── Tab: Audit Log ────────────────────────────────────────────

    def _build_audit_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("GLOBAL AUDIT LOG", "#10B981"))
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
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(16)

        v.addWidget(_section_label("APPLICATION CONFIGURATION", "#10B981"))

        warning = QLabel(
            "⚠  Safe configuration values only. Passwords, private keys, certificates, "
            "and other credentials are never displayed."
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

        lbl_style = "color: #6B8CB8; font-size: 12px;"
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

    # ── Data Loaders & Handlers ───────────────────────────────────

    def _refresh_all(self):
        self._refresh_hospitals()
        self._refresh_users()
        self._refresh_audit()
        self._refresh_config()

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

    def _on_create_hosp_admin(self):
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
            self._ca_status.setText(f"Hospital Admin created. User ID: {result}")
            for f in (self._ca_username, self._ca_password,
                      self._ca_confirm, self._ca_fname,
                      self._ca_lname, self._ca_empid):
                f.clear()
            self._refresh_users()
        else:
            self._ca_status.setStyleSheet("color: #EF4444; font-size: 12px;")
            self._ca_status.setText(f"Error: {result}")


# ─── Hospital Admin View ──────────────────────────────────────────

class HospitalAdminView(QWidget):
    """Internal Hospital Admin panel with 5 sections:

    1. My Hospital: Overview card for the admin's assigned hospital
    2. Users: Hospital-scoped user table (enable/disable, change role)
    3. Create User: Form to create technician/surgeon/doctor/observer
    4. Departments: Department list + inline create form
    5. Audit Log: Hospital-scoped audit events

    Strict security invariant: NO hospital selector exists.
    All hospital context is derived from the authenticated session.
    """

    def __init__(self, session_id: str, auth_manager: Optional[AuthManager] = None, parent=None):
        super().__init__(parent)
        self._session_id = session_id
        self._auth = auth_manager
        db = auth_manager.db if auth_manager else None
        self._svc = HospitalAdminService(db=db)

        self._build_ui()
        self._refresh_all()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: #0A0E14; }"
            "QTabBar::tab { background: #111820; color: #4A7BB8; "
            "  padding: 10px 24px; font-size: 13px; font-weight: 600; "
            "  font-family: 'Inter','Segoe UI',sans-serif; "
            "  border-bottom: 2px solid transparent; }"
            "QTabBar::tab:selected { color: #E6EDF3; "
            "  border-bottom: 2px solid #0095FF; background: #0A0E14; }"
            "QTabBar::tab:hover { color: #A0B4C8; background: #0D1117; }"
        )
        root.addWidget(self._tabs, 1)

        self._tabs.addTab(self._build_hospital_tab(), "My Hospital")
        self._tabs.addTab(self._build_users_tab(), "Users")
        self._tabs.addTab(self._build_create_user_tab(), "Create User")
        self._tabs.addTab(self._build_departments_tab(), "Departments")
        self._tabs.addTab(self._build_audit_tab(), "Audit Log")

    # ── Tab: My Hospital ──────────────────────────────────────────

    def _build_hospital_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(16)
        v.addWidget(_section_label("MY HOSPITAL", "#0095FF"))

        card = QFrame()
        card.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        form = QFormLayout(card)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)

        self._h_name   = QLabel("—")
        self._h_code   = QLabel("—")
        self._h_status = QLabel("—")
        self._h_addr   = QLabel("—")
        self._h_users  = QLabel("—")
        self._h_depts  = QLabel("—")

        _info_row("Hospital Name", self._h_name,   form)
        _info_row("Code",          self._h_code,   form)
        _info_row("Status",        self._h_status, form)
        _info_row("Address",       self._h_addr,   form)
        _info_row("Users",         self._h_users,  form)
        _info_row("Departments",   self._h_depts,  form)

        v.addWidget(card)
        v.addStretch()

        btn_ref = _action_btn("↻ Refresh", "#374151")
        btn_ref.clicked.connect(self._refresh_hospital_tab)
        v.addWidget(btn_ref)
        return w

    # ── Tab: Users ────────────────────────────────────────────────

    def _build_users_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("HOSPITAL USERS", "#0095FF"))
        top.addStretch()
        self._btn_enable  = _action_btn("Enable",  "#10B981")
        self._btn_disable = _action_btn("Disable", "#EF4444")
        btn_ref_u = _action_btn("↻ Refresh", "#374151")
        for b in (btn_ref_u, self._btn_enable, self._btn_disable):
            top.addWidget(b)
        v.addLayout(top)

        note = QLabel(
            "Role changes and enable/disable operations are validated server-side. "
            "You can only manage users in your own hospital."
        )
        note.setStyleSheet("color: #4A5568; font-size: 11px;")
        v.addWidget(note)

        self._user_table = _make_table(
            ["Username", "User ID", "Role", "Enabled", "Created"])
        v.addWidget(self._user_table, 1)

        # Role change row
        role_row = QHBoxLayout()
        lbl_change = QLabel("Change selected user's role to:")
        lbl_change.setStyleSheet("color: #6B8CB8; font-size: 12px;")
        role_row.addWidget(lbl_change)
        self._role_combo = QComboBox()
        self._role_combo.setFixedHeight(34)
        self._role_combo.setStyleSheet(
            "QComboBox { background: #161B22; color: #E6EDF3; "
            "border: 1px solid #2D3748; border-radius: 6px; "
            "padding: 0 12px; font-size: 13px; }"
        )
        for r in _ALLOWED_ROLES_LIST:
            self._role_combo.addItem(r)
        btn_change_role = _action_btn("Apply Role Change", "#0095FF")
        role_row.addWidget(self._role_combo)
        role_row.addWidget(btn_change_role)
        role_row.addStretch()
        v.addLayout(role_row)

        self._btn_enable.clicked.connect(lambda: self._on_set_user_enabled(True))
        self._btn_disable.clicked.connect(lambda: self._on_set_user_enabled(False))
        btn_ref_u.clicked.connect(self._refresh_users)
        btn_change_role.clicked.connect(self._on_change_role)
        return w

    # ── Tab: Create User ──────────────────────────────────────────

    def _build_create_user_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        v.addWidget(_section_label("CREATE HOSPITAL USER", "#0095FF"))
        v.addSpacing(6)

        allowed_str = ", ".join(sorted(HOSPITAL_ADMIN_ALLOWED_ROLES))
        note = QLabel(
            f"Allowed roles: {allowed_str}\n"
            "You cannot create app_admin or hospital_admin accounts."
        )
        note.setStyleSheet("color: #4A5568; font-size: 12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addSpacing(8)

        form_frame = QFrame()
        form_frame.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        form = QFormLayout(form_frame)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)

        lbl_style = "color: #4A7BB8; font-size: 12px;"

        self._cu_role = QComboBox()
        self._cu_role.setFixedHeight(36)
        self._cu_role.setStyleSheet(
            "QComboBox { background: #161B22; color: #E6EDF3; "
            "border: 1px solid #2D3748; border-radius: 6px; "
            "padding: 0 12px; font-size: 13px; }"
        )
        for r in _ALLOWED_ROLES_LIST:
            self._cu_role.addItem(r)

        self._cu_username = _field_input("Username")
        self._cu_password = _field_input("Password (min 8 chars)", password=True)
        self._cu_confirm  = _field_input("Confirm password", password=True)
        self._cu_fname    = _field_input("First name")
        self._cu_lname    = _field_input("Last name")
        self._cu_empid    = _field_input("Employee ID")

        for lbl_text, widget in [
            ("Role *",       self._cu_role),
            ("Username *",   self._cu_username),
            ("Password *",   self._cu_password),
            ("Confirm *",    self._cu_confirm),
            ("First Name *", self._cu_fname),
            ("Last Name",    self._cu_lname),
            ("Employee ID",  self._cu_empid),
        ]:
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(lbl_style)
            form.addRow(lbl, widget)

        self._cu_status = QLabel("")
        self._cu_status.setWordWrap(True)
        self._cu_status.setStyleSheet("color: #EF4444; font-size: 12px;")
        form.addRow(self._cu_status)

        self._cu_btn = _action_btn("Create User", "#0095FF")
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._cu_btn)
        form.addRow(btn_row)

        v.addWidget(form_frame)
        v.addStretch()

        self._cu_btn.clicked.connect(self._on_create_user)
        return w

    # ── Tab: Departments ──────────────────────────────────────────

    def _build_departments_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        v.addWidget(_section_label("DEPARTMENTS", "#0095FF"))

        top = QHBoxLayout()
        top.addStretch()
        btn_ref_d = _action_btn("↻ Refresh", "#374151")
        btn_ref_d.clicked.connect(self._refresh_departments)
        top.addWidget(btn_ref_d)
        v.addLayout(top)

        self._dept_table = _make_table(["Name", "Code", "Created"])
        v.addWidget(self._dept_table, 1)

        v.addWidget(_section_label("CREATE DEPARTMENT", "#0095FF"))
        create_frame = QFrame()
        create_frame.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        cf = QFormLayout(create_frame)
        cf.setContentsMargins(20, 16, 20, 16)
        cf.setSpacing(12)

        lbl_style = "color: #4A7BB8; font-size: 12px;"
        self._dept_name = _field_input("e.g. Cardiology")
        self._dept_code = _field_input("e.g. CARD (unique per hospital)")

        for t, w2 in [("Name *", self._dept_name), ("Code *", self._dept_code)]:
            l = QLabel(t)
            l.setStyleSheet(lbl_style)
            cf.addRow(l, w2)

        self._dept_status = QLabel("")
        self._dept_status.setStyleSheet("color: #EF4444; font-size: 12px;")
        cf.addRow(self._dept_status)

        btn_dept = _action_btn("Create Department", "#0095FF")
        btn_dept.clicked.connect(self._on_create_dept)
        btn_r2 = QHBoxLayout()
        btn_r2.addStretch()
        btn_r2.addWidget(btn_dept)
        cf.addRow(btn_r2)
        v.addWidget(create_frame)
        return w

    # ── Tab: Audit Log ────────────────────────────────────────────

    def _build_audit_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(_section_label("HOSPITAL AUDIT LOG", "#0095FF"))
        top.addStretch()
        btn_ref = _action_btn("↻ Refresh", "#374151")
        btn_ref.clicked.connect(self._refresh_audit)
        top.addWidget(btn_ref)
        v.addLayout(top)

        note = QLabel("Showing audit events for users in your hospital only.")
        note.setStyleSheet("color: #4A5568; font-size: 11px;")
        v.addWidget(note)

        self._audit_table = _make_table(
            ["Timestamp", "Event", "Username", "Details"])
        v.addWidget(self._audit_table, 1)
        return w

    # ── Data Loaders & Handlers ───────────────────────────────────

    def _refresh_all(self):
        self._refresh_hospital_tab()
        self._refresh_users()
        self._refresh_departments()
        self._refresh_audit()

    def _refresh_hospital_tab(self):
        ok, data = self._svc.get_my_hospital(self._session_id)
        if not ok or not isinstance(data, dict):
            return
        status = "Active" if data.get("is_active") else "Inactive"
        s_color = "#10B981" if data.get("is_active") else "#EF4444"
        self._h_name.setText(data.get("name", ""))
        self._h_code.setText(data.get("code", ""))
        self._h_status.setText(status)
        self._h_status.setStyleSheet(
            f"color: {s_color}; font-size: 13px; font-weight: 700;"
        )
        self._h_addr.setText(data.get("address", "") or "—")
        self._h_users.setText(str(data.get("user_count", 0)))
        self._h_depts.setText(str(data.get("dept_count", 0)))

    def _refresh_users(self):
        ok, data = self._svc.list_my_users(self._session_id)
        if not ok:
            return
        self._user_data = data
        self._user_table.setRowCount(0)
        for u in data:
            row = self._user_table.rowCount()
            self._user_table.insertRow(row)
            enabled = "Yes" if u.get("is_enabled") else "No"
            for col, val in enumerate([
                u.get("username", ""),
                u.get("user_id", "") or "",
                u.get("role", ""),
                enabled,
                (u.get("created_at", "") or "")[:10],
            ]):
                item = QTableWidgetItem(str(val))
                if col == 3:
                    item.setForeground(
                        QColor("#10B981") if val == "Yes" else QColor("#EF4444"))
                self._user_table.setItem(row, col, item)

    def _refresh_departments(self):
        ok, data = self._svc.list_departments(self._session_id)
        if not ok:
            return
        self._dept_table.setRowCount(0)
        for d in data:
            row = self._dept_table.rowCount()
            self._dept_table.insertRow(row)
            for col, val in enumerate([
                d.get("name", ""), d.get("code", ""),
                (d.get("created_at", "") or "")[:10],
            ]):
                self._dept_table.setItem(row, col, QTableWidgetItem(str(val)))

    def _refresh_audit(self):
        ok, data = self._svc.get_hospital_audit_logs(self._session_id, limit=200)
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
                entry.get("details", "") or "",
            ]):
                self._audit_table.setItem(row, col, QTableWidgetItem(str(val)))

    def _get_selected_username(self) -> Optional[str]:
        row = self._user_table.currentRow()
        if row < 0 or not hasattr(self, "_user_data") or row >= len(self._user_data):
            return None
        return self._user_data[row].get("username")

    def _on_set_user_enabled(self, enabled: bool):
        target = self._get_selected_username()
        if not target:
            QMessageBox.information(self, "Select User",
                                    "Please select a user first.")
            return
        action = "enable" if enabled else "disable"
        reply = QMessageBox.question(
            self, f"Confirm {action.title()}",
            f"Are you sure you want to {action} user:\n\n  {target}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, err = self._svc.set_user_enabled(self._session_id, target, enabled)
        if ok:
            self._refresh_users()
        else:
            QMessageBox.warning(self, "Error", err)

    def _on_change_role(self):
        target = self._get_selected_username()
        if not target:
            QMessageBox.information(self, "Select User",
                                    "Please select a user first.")
            return
        new_role = self._role_combo.currentText()
        reply = QMessageBox.question(
            self, "Confirm Role Change",
            f"Change role of '{target}' to '{new_role}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, err = self._svc.set_user_role(self._session_id, target, new_role)
        if ok:
            self._refresh_users()
        else:
            QMessageBox.warning(self, "Error", err)

    def _on_create_user(self):
        self._cu_status.setText("")
        username = self._cu_username.text().strip()
        password = self._cu_password.text()
        confirm  = self._cu_confirm.text()
        fname    = self._cu_fname.text().strip()
        lname    = self._cu_lname.text().strip()
        empid    = self._cu_empid.text().strip()
        role     = self._cu_role.currentText()

        if not username:
            self._cu_status.setText("Username is required.")
            return
        if len(password) < 8:
            self._cu_status.setText("Password must be at least 8 characters.")
            return
        if password != confirm:
            self._cu_status.setText("Passwords do not match.")
            return
        if not fname:
            self._cu_status.setText("First name is required.")
            return

        ok, result = self._svc.create_user(
            self._session_id, username, password, role,
            fname, lname, empid)
        if ok:
            self._cu_status.setStyleSheet("color: #10B981; font-size: 12px;")
            self._cu_status.setText(f"User created. User ID: {result}")
            for f in (self._cu_username, self._cu_password,
                      self._cu_confirm, self._cu_fname,
                      self._cu_lname, self._cu_empid):
                f.clear()
            self._refresh_users()
            self._refresh_hospital_tab()
        else:
            self._cu_status.setStyleSheet("color: #EF4444; font-size: 12px;")
            self._cu_status.setText(f"Error: {result}")

    def _on_create_dept(self):
        self._dept_status.setText("")
        name = self._dept_name.text().strip()
        code = self._dept_code.text().strip()
        if not name or not code:
            self._dept_status.setText("Name and Code are required.")
            return
        ok, result = self._svc.create_department(self._session_id, name, code)
        if ok:
            self._dept_status.setStyleSheet("color: #10B981; font-size: 12px;")
            self._dept_status.setText(f"Department created (ID: {result})")
            self._dept_name.clear()
            self._dept_code.clear()
            self._refresh_departments()
            self._refresh_hospital_tab()
        else:
            self._dept_status.setStyleSheet("color: #EF4444; font-size: 12px;")
            self._dept_status.setText(f"Error: {result}")


# ─── Unified UserManagementScreen ─────────────────────────────────

class UserManagementScreen(QWidget):
    """Unified User Management screen hosted inside AetherConsole.

    Renders either AppAdminView or HospitalAdminView based on the mode.
    Security: The mode must be determined server-side via AuthorizationService.
    """

    def __init__(self, session_id: str, role: str,
                 auth_manager: Optional[AuthManager] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("UserManagementScreen")
        self._session_id = session_id
        self._role = role
        self._auth_manager = auth_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if role == "app_admin":
            self.view = AppAdminView(session_id, auth_manager=auth_manager, parent=self)
            layout.addWidget(self.view)
        elif role == "hospital_admin":
            self.view = HospitalAdminView(session_id, auth_manager=auth_manager, parent=self)
            layout.addWidget(self.view)
        else:
            # Fallback for unauthorized roles (defense-in-depth)
            lbl = QLabel("Access Denied: Administrative privileges required.")
            lbl.setStyleSheet("color: #EF4444; font-size: 14px; font-weight: bold; padding: 24px;")
            layout.addWidget(lbl)
