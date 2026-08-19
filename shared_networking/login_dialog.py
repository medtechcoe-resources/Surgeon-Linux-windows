# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — LOGIN DIALOG
#  Professional medical-grade modal login screen.
#  Shared by all three applications (Surgeon, Robot, Observer).
#  No registration, sign-up, or forgot-password functionality.
#  Theme-aware: responds to ThemeManager dark/light switch.
# ═══════════════════════════════════════════════════════════════════

import math
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QWidget,
)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QLinearGradient

from shared_networking.authentication import AuthManager
from theme_manager import ThemeManager


# ─── Painted Logo Badge (same style as header, reused here) ───────────────

class _LoginLogoBadge(QWidget):
    """Medical cross shield icon — theme-aware, painted inline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 52)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._on_theme)
        self._refresh_colors(tm.current)

    def _on_theme(self, theme):
        self._refresh_colors(theme)
        self.update()

    def _refresh_colors(self, theme):
        if theme == "dark":
            self._bg = QColor("#0D2818")
            self._border = QColor("#10B981")
            self._cross = QColor("#10B981")
        else:
            self._bg = QColor("#ECFDF5")
            self._border = QColor("#059669")
            self._cross = QColor("#059669")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._bg)
        p.setPen(QPen(self._border, 2.5))
        p.drawRoundedRect(2, 2, 48, 48, 12, 12)
        p.setPen(QPen(self._cross, 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(26, 15, 26, 37)
        p.drawLine(16, 26, 36, 26)
        p.end()


class LoginDialog(QDialog):
    """Modal login dialog for Aether Console applications.

    Usage:
        dialog = LoginDialog(title="Surgeon Console — Login")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            username = dialog.username
            role = dialog.role
            session_id = dialog.session_id
    """

    def __init__(self, title: str = "Aether Console — Login",
                 auth_manager: AuthManager = None, parent=None):
        super().__init__(parent)
        self._auth = auth_manager or AuthManager()
        self._title_text = title

        # Result properties
        self.username = ""
        self.role = ""
        self.session_id = ""

        self.setWindowTitle(title)
        self.setFixedSize(460, 560)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(True)

        self._build_ui()

        # Subscribe to theme changes and apply initial theme
        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._on_theme_changed)
        self._apply_styles(tm.current)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Background frame
        self._bg = QFrame()
        self._bg.setObjectName("LoginBackground")
        main = QVBoxLayout(self._bg)
        main.setContentsMargins(44, 36, 44, 36)
        main.setSpacing(0)

        # ── Top accent bar ──
        self._accent_bar = QFrame()
        self._accent_bar.setFixedHeight(3)
        self._accent_bar.setObjectName("LoginAccent")
        main.addWidget(self._accent_bar)
        main.addSpacing(28)

        # ── Logo ──
        logo_row = QHBoxLayout()
        logo_row.addStretch()
        self._logo = _LoginLogoBadge()
        logo_row.addWidget(self._logo)
        logo_row.addStretch()
        main.addLayout(logo_row)
        main.addSpacing(14)

        # ── System name ──
        system_label = QLabel("AETHER SURGICAL CONSOLE")
        system_label.setObjectName("LoginSystemName")
        system_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(system_label)
        main.addSpacing(6)

        # ── Title ──
        title_label = QLabel(self._title_text)
        title_label.setObjectName("LoginTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(title_label)
        main.addSpacing(4)

        # ── Subtitle ──
        sub = QLabel("Secure Authentication Required")
        sub.setObjectName("LoginSubtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(sub)
        main.addSpacing(24)

        # ── Separator ──
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setObjectName("LoginSeparator")
        main.addWidget(sep)
        main.addSpacing(22)

        # ── Username field ──
        user_label = QLabel("USERNAME")
        user_label.setObjectName("LoginFieldLabel")
        main.addWidget(user_label)
        main.addSpacing(6)

        self._username_input = QLineEdit()
        self._username_input.setObjectName("LoginInput")
        self._username_input.setPlaceholderText("Enter username")
        self._username_input.setFixedHeight(44)
        main.addWidget(self._username_input)
        main.addSpacing(14)

        # ── Password field ──
        pw_label = QLabel("PASSWORD")
        pw_label.setObjectName("LoginFieldLabel")
        main.addWidget(pw_label)
        main.addSpacing(6)

        self._password_input = QLineEdit()
        self._password_input.setObjectName("LoginInput")
        self._password_input.setPlaceholderText("Enter password")
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_input.setFixedHeight(44)
        self._password_input.returnPressed.connect(self._on_login)
        main.addWidget(self._password_input)
        main.addSpacing(10)

        # ── Error message ──
        self._error_label = QLabel("")
        self._error_label.setObjectName("LoginError")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        main.addWidget(self._error_label)
        main.addSpacing(8)

        # ── Login button ──
        self._login_btn = QPushButton("LOGIN")
        self._login_btn.setObjectName("LoginButton")
        self._login_btn.setFixedHeight(46)
        self._login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._login_btn.clicked.connect(self._on_login)
        main.addWidget(self._login_btn)

        main.addStretch()

        # ── Footer ──
        footer = QLabel("Aether Surgical Robotics  ·  Secure LAN Access")
        footer.setObjectName("LoginFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(footer)

        outer.addWidget(self._bg)

    def _on_login(self):
        """Handle login button click."""
        username = self._username_input.text().strip()
        password = self._password_input.text()

        if not username:
            self._show_error("Username cannot be empty.")
            self._username_input.setFocus()
            return

        if not password:
            self._show_error("Password cannot be empty.")
            self._password_input.setFocus()
            return

        success, role = self._auth.verify(username, password)

        if success:
            self.username = username
            self.role = role
            self.session_id = self._auth.create_session(username, role)
            self.accept()
        else:
            self._show_error("Invalid username or password.")
            self._password_input.clear()
            self._password_input.setFocus()

    def _show_error(self, message: str):
        self._error_label.setText(message)
        self._error_label.setVisible(True)

    def _on_theme_changed(self, theme: str):
        self._apply_styles(theme)

    def _apply_styles(self, theme: str):
        """Apply theme-appropriate styling."""
        if theme == "dark":
            self.setStyleSheet("""
                QDialog {
                    background-color: #0D1117;
                }
                #LoginBackground {
                    background-color: #0D1117;
                    border: none;
                }
                #LoginAccent {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #0095FF, stop:0.5 #10B981, stop:1 #0095FF
                    );
                    border-radius: 1px;
                }
                #LoginSystemName {
                    background: transparent;
                    color: #4A5568;
                    font-size: 10px;
                    font-weight: 600;
                    letter-spacing: 3px;
                    font-family: 'JetBrains Mono', 'Consolas', monospace;
                }
                #LoginTitle {
                    background: transparent;
                    color: #F5F7FA;
                    font-size: 16px;
                    font-weight: 700;
                    letter-spacing: 0.5px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginSubtitle {
                    background: transparent;
                    color: #4A5568;
                    font-size: 12px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginSeparator {
                    background-color: #1C2333;
                }
                #LoginFieldLabel {
                    background: transparent;
                    color: #6B7B8D;
                    font-size: 11px;
                    font-weight: 700;
                    letter-spacing: 1.5px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginInput {
                    background-color: #161B22;
                    color: #E6EDF3;
                    border: 1px solid #2D3748;
                    border-radius: 7px;
                    padding: 0 14px;
                    font-size: 14px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginInput:focus {
                    border: 1.5px solid #0095FF;
                }
                #LoginError {
                    background-color: rgba(239, 68, 68, 0.08);
                    color: #EF4444;
                    font-size: 12px;
                    font-weight: 600;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                    border: 1px solid rgba(239, 68, 68, 0.25);
                    border-radius: 6px;
                    padding: 8px;
                }
                #LoginButton {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #0077CC, stop:1 #0095FF
                    );
                    color: #FFFFFF;
                    border: none;
                    border-radius: 7px;
                    font-size: 14px;
                    font-weight: 700;
                    letter-spacing: 2px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginButton:hover {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #0095FF, stop:1 #1AA3FF
                    );
                }
                #LoginButton:pressed {
                    background-color: #005FA3;
                }
                #LoginFooter {
                    background: transparent;
                    color: #2D3748;
                    font-size: 10px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
            """)
        else:
            # Light mode
            self.setStyleSheet("""
                QDialog {
                    background-color: #F1F5F9;
                }
                #LoginBackground {
                    background-color: #FFFFFF;
                    border: 1px solid #E2E8F0;
                    border-radius: 0px;
                }
                #LoginAccent {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #0077CC, stop:0.5 #059669, stop:1 #0077CC
                    );
                    border-radius: 1px;
                }
                #LoginSystemName {
                    background: transparent;
                    color: #94A3B8;
                    font-size: 10px;
                    font-weight: 600;
                    letter-spacing: 3px;
                    font-family: 'JetBrains Mono', 'Consolas', monospace;
                }
                #LoginTitle {
                    background: transparent;
                    color: #0F172A;
                    font-size: 16px;
                    font-weight: 700;
                    letter-spacing: 0.5px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginSubtitle {
                    background: transparent;
                    color: #64748B;
                    font-size: 12px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginSeparator {
                    background-color: #E2E8F0;
                }
                #LoginFieldLabel {
                    background: transparent;
                    color: #475569;
                    font-size: 11px;
                    font-weight: 700;
                    letter-spacing: 1.5px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginInput {
                    background-color: #F8FAFC;
                    color: #0F172A;
                    border: 1px solid #CBD5E1;
                    border-radius: 7px;
                    padding: 0 14px;
                    font-size: 14px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginInput:focus {
                    border: 1.5px solid #0077CC;
                    background-color: #FFFFFF;
                }
                #LoginError {
                    background-color: #FEF2F2;
                    color: #DC2626;
                    font-size: 12px;
                    font-weight: 600;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                    border: 1px solid #FECACA;
                    border-radius: 6px;
                    padding: 8px;
                }
                #LoginButton {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #005EA3, stop:1 #0077CC
                    );
                    color: #FFFFFF;
                    border: none;
                    border-radius: 7px;
                    font-size: 14px;
                    font-weight: 700;
                    letter-spacing: 2px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
                #LoginButton:hover {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #0077CC, stop:1 #0095FF
                    );
                }
                #LoginButton:pressed {
                    background-color: #004A80;
                }
                #LoginFooter {
                    background: transparent;
                    color: #94A3B8;
                    font-size: 10px;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }
            """)
