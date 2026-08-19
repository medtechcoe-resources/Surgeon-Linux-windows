"""
Header widget — Aether Surgical Console.
Contains: brand logo, system status chips, E-Stop button (inline), theme toggle.
"""
import math
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
                             QFrame, QPushButton, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QDateTime, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF
from PyQt6.QtCore import QPointF

from widgets.estop import EmergencyStopButton
from theme_manager import ThemeManager


# ─── Painted Logo Badge ────────────────────────────────────────────────────

class _LogoBadge(QWidget):
    """Medical cross shield icon — theme-aware."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
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
        p.setPen(QPen(self._border, 2))
        p.drawRoundedRect(2, 2, 40, 40, 10, 10)
        p.setPen(QPen(self._cross, 3.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(22, 13, 22, 31)
        p.drawLine(14, 22, 30, 22)
        p.end()


# ─── Painted Status Dot ────────────────────────────────────────────────────

class _StatusDot(QWidget):
    def __init__(self, color: str, size=12, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size + 4, size + 4)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        off = (self.width() - self._size) // 2
        p.drawEllipse(off, off, self._size, self._size)
        p.end()


# ─── Theme Toggle Icon Button ──────────────────────────────────────────────

class _ThemeButton(QPushButton):
    """Sun/Moon icon theme toggle — theme-aware painting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ThemeToggle")
        self.setFixedSize(100, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText("")
        tm = ThemeManager.instance()
        self._is_dark = tm.is_dark()
        tm.theme_changed.connect(self._on_theme)

    def _on_theme(self, theme):
        self._is_dark = (theme == "dark")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tm = ThemeManager.instance()
        text_color = QColor(tm.color("fg_primary"))
        icon_color = QColor(tm.color("accent_amber"))

        cx, cy = 22, self.height() // 2

        if self._is_dark:
            # Moon icon
            p.setBrush(icon_color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - 9, cy - 9, 18, 18)
            p.setBrush(QColor(tm.color("bg_chip")))
            p.drawEllipse(cx - 4, cy - 11, 18, 18)
            label = "LIGHT"
        else:
            # Sun icon
            p.setBrush(icon_color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - 7, cy - 7, 14, 14)
            pen = QPen(icon_color, 2.5, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for i in range(8):
                angle = math.radians(i * 45)
                x1 = cx + 10 * math.cos(angle)
                y1 = cy + 10 * math.sin(angle)
                x2 = cx + 14 * math.cos(angle)
                y2 = cy + 14 * math.sin(angle)
                p.drawLine(int(x1), int(y1), int(x2), int(y2))
            label = "DARK"

        p.setPen(text_color)
        font = QFont("Inter", 11, QFont.Weight.Bold)
        p.setFont(font)
        text_rect = self.rect().adjusted(34, 0, -4, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   label)
        p.end()


# ─── Header ───────────────────────────────────────────────────────────────

class Header(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Header")
        self.setFixedHeight(58)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        # ── Brand ──
        badge = _LogoBadge()
        layout.addWidget(badge)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(2)
        title = QLabel("AETHER  ·  SURGICAL")
        title.setObjectName("BrandTitle")
        sub = QLabel("ROBOTIC CONSOLE  ·  REV 4.2")
        sub.setObjectName("BrandSubtitle")
        brand_box.addWidget(title)
        brand_box.addWidget(sub)
        layout.addLayout(brand_box)

        layout.addStretch()

        # ── Time chip ──
        time_chip = QFrame()
        time_chip.setObjectName("HeaderChip")
        tc_layout = QHBoxLayout(time_chip)
        tc_layout.setContentsMargins(18, 8, 18, 8)
        tc_layout.setSpacing(24)

        self.local_box = self._time_block("LOCAL")
        self.utc_box = self._time_block("UTC", accent=True)
        tc_layout.addLayout(self.local_box["layout"])
        tc_layout.addLayout(self.utc_box["layout"])
        layout.addWidget(time_chip)

        # ── System Status chip ──
        sys_chip = QFrame()
        sys_chip.setObjectName("HeaderChip")
        sc_layout = QHBoxLayout(sys_chip)
        sc_layout.setContentsMargins(14, 8, 14, 8)
        sc_layout.setSpacing(8)

        self._sys_dot = _StatusDot("#10B981")
        sc_layout.addWidget(self._sys_dot)

        sys_text = QVBoxLayout()
        sys_text.setSpacing(2)
        l1 = QLabel("SYSTEM")
        l1.setObjectName("HeaderChipLabel")
        self._sys_status = QLabel("ACTIVE")
        self._sys_status.setObjectName("SystemActiveLabel")
        sys_text.addWidget(l1)
        sys_text.addWidget(self._sys_status)
        sc_layout.addLayout(sys_text)
        layout.addWidget(sys_chip)

        # ── Registration Status chip ──
        reg_chip = QFrame()
        reg_chip.setObjectName("HeaderChip")
        rc_layout = QHBoxLayout(reg_chip)
        rc_layout.setContentsMargins(14, 8, 14, 8)
        rc_layout.setSpacing(8)
        reg_dot = _StatusDot("#10B981")
        rc_layout.addWidget(reg_dot)
        reg_text = QVBoxLayout()
        reg_text.setSpacing(2)
        rl1 = QLabel("REGISTRATION")
        rl1.setObjectName("HeaderChipLabel")
        rl2 = QLabel("ACTIVE")
        rl2.setObjectName("SystemActiveLabel")
        reg_text.addWidget(rl1)
        reg_text.addWidget(rl2)
        rc_layout.addLayout(reg_text)
        layout.addWidget(reg_chip)

        # ── Theme toggle ──
        self.theme_btn = _ThemeButton()
        layout.addWidget(self.theme_btn)

        # ── Emergency Stop (inline, top-right) ──
        self.estop = EmergencyStopButton()
        layout.addWidget(self.estop)

        # ── Clock timer ──
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_time)
        self.timer.start(1000)
        self._update_time()

    def _time_block(self, label, accent=False):
        layout = QVBoxLayout()
        layout.setSpacing(2)
        lab = QLabel(label)
        lab.setObjectName("HeaderChipLabel")
        val = QLabel("--:--:--")
        val.setObjectName("HeaderChipValueAccent" if accent else "HeaderChipValue")
        layout.addWidget(lab)
        layout.addWidget(val)
        return {"layout": layout, "label": lab, "value": val}

    def _update_time(self):
        now_local = QDateTime.currentDateTime()
        now_utc = QDateTime.currentDateTimeUtc()
        self.local_box["value"].setText(now_local.toString("ddd, MMM dd yyyy"))
        self.utc_box["value"].setText(now_utc.toString("HH:mm:ss"))
