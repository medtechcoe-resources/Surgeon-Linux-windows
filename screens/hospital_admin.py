# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — HOSPITAL ADMIN PANEL (Phase 4)
#
#  Standalone QMainWindow for the hospital_admin role.
#  Opened exclusively by admin_launcher.route_after_login() when
#  the authenticated role is 'hospital_admin'.
#
#  Tabs:
#    1. My Hospital   — read-only hospital info + stats
#    2. Users         — hospital-scoped user table with enable/role controls
#    3. Create User   — form for technician/surgeon/doctor/observer only
#    4. Departments   — department list + create form
#    5. Audit Log     — hospital-scoped audit events only
#
#  Security rules:
#    - Hospital identity derived from session — never from UI state.
#    - All operations go through HospitalAdminService (RBAC enforced).
#    - Allowed user roles: {technician, surgeon, doctor, observer} only.
#    - Role escalation (hospital_admin, app_admin) is rejected server-side.
#    - Admin cannot disable or change role of own account.
#    - Logout invalidates the session; closeEvent does the same.
#    - No password-reset UI (Phase 6).
# ═══════════════════════════════════════════════════════════════════

import logging

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QFrame, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFormLayout, QComboBox, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from theme_manager import ThemeManager
from shared_networking.authentication import AuthManager
from shared_networking.admin_service import (
    HospitalAdminService, HOSPITAL_ADMIN_ALLOWED_ROLES,
)

log = logging.getLogger(__name__)

# Sorted list for dropdowns
_ALLOWED_ROLES_LIST = sorted(HOSPITAL_ADMIN_ALLOWED_ROLES)


# ─── Small UI helpers (same style as app_admin.py) ───────────────

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: #0095FF; font-size: 11px; font-weight: 700; "
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
        "QTableWidget::item:selected { background: #1C3A6A; }"
        "QHeaderView::section { background: #0D1A2E; color: #4A7BB8; "
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
        f"QPushButton:hover {{ opacity: 0.85; }}"
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


# ─── Main Hospital Admin Window ───────────────────────────────────

class HospitalAdminWindow(QMainWindow):
    """Standalone Hospital Admin Panel.

    Opened exclusively for role='hospital_admin'. All data operations
    go through HospitalAdminService, which enforces RBAC and hospital
    isolation on every call. The hospital context is read from the
    session — never from UI state.
    """

    def __init__(self, username: str, session_id: str,
                 auth_manager: AuthManager, parent=None):
        super().__init__(parent)
        self._username  = username
        self._session_id = session_id
        self._auth      = auth_manager
        self._svc       = HospitalAdminService()
        self._logged_out = False
        self._hospital_name = ""

        self.setWindowTitle("AETHER — Hospital Admin Console")
        self.resize(1300, 860)

        # Load hospital info first so the header can display it
        self._load_hospital_info()
        self._build_ui()
        self._refresh_all()

        ThemeManager.instance().theme_changed.connect(self._apply_base_style)
        self._apply_base_style(ThemeManager.instance().current)

    def _load_hospital_info(self):
        ok, data = self._svc.get_my_hospital(self._session_id)
        if ok and isinstance(data, dict):
            self._hospital_name = data.get("name", "Unknown Hospital")
            self._hospital_data = data
        else:
            self._hospital_name = "Hospital"
            self._hospital_data = {}

    # ─── UI Construction ──────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_header())

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

        self._tabs.addTab(self._build_hospital_tab(), "  My Hospital  ")
        self._tabs.addTab(self._build_users_tab(), "  Users  ")
        self._tabs.addTab(self._build_create_user_tab(), "  Create User  ")
        self._tabs.addTab(self._build_departments_tab(), "  Departments  ")
        self._tabs.addTab(self._build_audit_tab(), "  Audit Log  ")

    def _make_header(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #060B11, stop:0.5 #060F1E, stop:1 #060B11);"
            "border-bottom: 1px solid #0095FF;"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 0, 24, 0)

        badge = QLabel("⬡")
        badge.setStyleSheet("color: #0095FF; font-size: 22px;")
        h.addWidget(badge)
        h.addSpacing(10)

        title = QLabel(f"AETHER  ·  HOSPITAL ADMIN  ·  {self._hospital_name.upper()}")
        title.setStyleSheet(
            "color: #E6EDF3; font-size: 14px; font-weight: 700; "
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

    # ── Tab: My Hospital ──────────────────────────────────────────

    def _build_hospital_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(16)
        v.addWidget(_section_label("MY HOSPITAL"))

        card = QFrame()
        card.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        form = QFormLayout(card)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)

        self._h_name   = QLabel()
        self._h_code   = QLabel()
        self._h_status = QLabel()
        self._h_addr   = QLabel()
        self._h_users  = QLabel()
        self._h_depts  = QLabel()

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
        top.addWidget(_section_label("HOSPITAL USERS"))
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
        note.setStyleSheet(
            "color: #4A5568; font-size: 11px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        v.addWidget(note)

        self._user_table = _make_table(
            ["Username", "User ID", "Role", "Enabled", "Created"])
        v.addWidget(self._user_table, 1)

        # Role change row
        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("Change selected user's role to:"))
        self._role_combo = QComboBox()
        self._role_combo.setFixedHeight(34)
        self._role_combo.setStyleSheet(
            "QComboBox { background: #161B22; color: #E6EDF3; "
            "border: 1px solid #2D3748; border-radius: 6px; "
            "padding: 0 12px; font-size: 13px; }"
        )
        for r in _ALLOWED_ROLES_LIST:
            self._role_combo.addItem(r)
        btn_change_role = _action_btn("Apply Role Change")
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
        v.addWidget(_section_label("CREATE HOSPITAL USER"))
        v.addSpacing(6)

        allowed_str = ", ".join(sorted(HOSPITAL_ADMIN_ALLOWED_ROLES))
        note = QLabel(
            f"Allowed roles: {allowed_str}\n"
            "You cannot create app_admin or hospital_admin accounts. "
            "Hospital Admin creation is an Application Admin operation."
        )
        note.setStyleSheet(
            "color: #4A5568; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
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

        lbl_style = (
            "color: #4A7BB8; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )

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

        self._cu_btn = _action_btn("Create User")
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
        v.addWidget(_section_label("DEPARTMENTS"))

        top = QHBoxLayout()
        top.addStretch()
        btn_ref_d = _action_btn("↻ Refresh", "#374151")
        btn_ref_d.clicked.connect(self._refresh_departments)
        top.addWidget(btn_ref_d)
        v.addLayout(top)

        self._dept_table = _make_table(["Name", "Code", "Created"])
        v.addWidget(self._dept_table, 1)

        # Inline create form
        v.addWidget(_section_label("CREATE DEPARTMENT"))
        create_frame = QFrame()
        create_frame.setStyleSheet(
            "background: #111820; border: 1px solid #1C2333; border-radius: 8px;"
        )
        cf = QFormLayout(create_frame)
        cf.setContentsMargins(20, 16, 20, 16)
        cf.setSpacing(12)

        lbl_style = (
            "color: #4A7BB8; font-size: 12px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        self._dept_name = _field_input("e.g. Cardiology")
        self._dept_code = _field_input("e.g. CARD (unique per hospital)")

        for t, w2 in [("Name *", self._dept_name), ("Code *", self._dept_code)]:
            l = QLabel(t)
            l.setStyleSheet(lbl_style)
            cf.addRow(l, w2)

        self._dept_status = QLabel("")
        self._dept_status.setStyleSheet("color: #EF4444; font-size: 12px;")
        cf.addRow(self._dept_status)

        btn_dept = _action_btn("Create Department")
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
        top.addWidget(_section_label("HOSPITAL AUDIT LOG"))
        top.addStretch()
        btn_ref = _action_btn("↻ Refresh", "#374151")
        btn_ref.clicked.connect(self._refresh_audit)
        top.addWidget(btn_ref)
        v.addLayout(top)

        note = QLabel("Showing audit events for users in your hospital only.")
        note.setStyleSheet(
            "color: #4A5568; font-size: 11px; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
        )
        v.addWidget(note)

        self._audit_table = _make_table(
            ["Timestamp", "Event", "Username", "Details"])
        v.addWidget(self._audit_table, 1)
        return w

    # ─── Data Refresh ─────────────────────────────────────────────

    def _refresh_all(self):
        self._refresh_hospital_tab()
        self._refresh_users()
        self._refresh_departments()
        self._refresh_audit()

    def _refresh_hospital_tab(self):
        ok, data = self._svc.get_my_hospital(self._session_id)
        if not ok or not isinstance(data, dict):
            return
        self._hospital_data = data
        status = "Active" if data.get("is_active") else "Inactive"
        s_color = "#10B981" if data.get("is_active") else "#EF4444"
        self._h_name.setText(data.get("name", ""))
        self._h_code.setText(data.get("code", ""))
        self._h_status.setText(status)
        self._h_status.setStyleSheet(
            f"color: {s_color}; font-size: 13px; font-weight: 700; "
            "font-family: 'Inter','Segoe UI',sans-serif;"
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
                d.get("name", ""),
                d.get("code", ""),
                (d.get("created_at", "") or "")[:10],
            ]):
                self._dept_table.setItem(row, col, QTableWidgetItem(str(val)))

    def _refresh_audit(self):
        ok, data = self._svc.get_hospital_audit_logs(self._session_id, limit=100)
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

    # ─── Actions ──────────────────────────────────────────────────

    def _get_selected_username(self) -> str:
        row = self._user_table.currentRow()
        if row < 0 or not hasattr(self, "_user_data") or row >= len(self._user_data):
            return ""
        return self._user_data[row].get("username", "")

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

    # ─── Logout / Close ───────────────────────────────────────────

    def _on_logout(self):
        reply = QMessageBox.question(
            self, "Logout",
            "Are you sure you want to logout?\nYour session will be invalidated.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_logout()

    def _do_logout(self):
        if not self._logged_out:
            self._logged_out = True
            log.info(
                f"[HOSP_ADMIN] Logout: invalidating session for {self._username}"
            )
            self._auth.remove_session(self._session_id)
        self.close()

    def closeEvent(self, event):
        if not self._logged_out:
            self._logged_out = True
            log.info(
                f"[HOSP_ADMIN] Window closed: invalidating session for {self._username}"
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
