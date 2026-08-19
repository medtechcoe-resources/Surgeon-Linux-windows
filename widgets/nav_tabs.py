"""
Navigation tab bar — each tab has a QPainter-drawn icon + label.
No emojis. Icons are 18×18 px rendered inline.
Reduced to 4 tabs: Pre-Op, Live Video, Live Control, Settings.
"""
import math
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF

from theme_manager import ThemeManager


# ─── Icon painters (static functions, 18×18 canvas) ────────────────────────

def _draw_clipboard(p, cx, cy, c):
    """Pre-op: clipboard / patient file."""
    p.setPen(QPen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(int(cx - 7), int(cy - 8), 14, 16, 2, 2)
    p.drawLine(int(cx - 3), int(cy - 8), int(cx - 3), int(cy - 11))
    p.drawLine(int(cx + 3), int(cy - 8), int(cx + 3), int(cy - 11))
    p.drawLine(int(cx - 3), int(cy - 11), int(cx + 3), int(cy - 11))
    p.drawLine(int(cx - 4), int(cy - 2), int(cx + 4), int(cy - 2))
    p.drawLine(int(cx - 4), int(cy + 2), int(cx + 4), int(cy + 2))


def _draw_camera(p, cx, cy, c):
    """Live Video: endoscope / camera."""
    p.setPen(QPen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(int(cx - 8), int(cy - 5), 16, 11, 2, 2)
    p.drawEllipse(int(cx - 3), int(cy - 3), 6, 6)
    p.drawLine(int(cx - 3), int(cy - 8), int(cx - 1), int(cy - 5))
    p.drawLine(int(cx + 1), int(cy - 5), int(cx + 3), int(cy - 8))


def _draw_joystick(p, cx, cy, c):
    """Live Control: robotic arm / joystick."""
    p.setPen(QPen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(int(cx - 7), int(cy + 1), 14, 7)
    p.drawLine(int(cx), int(cy + 1), int(cx), int(cy - 6))
    p.drawEllipse(int(cx - 3), int(cy - 9), 6, 6)


def _draw_gear(p, cx, cy, c):
    """Settings: gear icon."""
    p.setPen(QPen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(int(cx - 4), int(cy - 4), 8, 8)
    p.drawEllipse(int(cx - 8), int(cy - 8), 16, 16)
    # Tick marks for gear teeth
    for i in range(8):
        angle = math.radians(i * 45)
        x1 = cx + 8 * math.cos(angle)
        y1 = cy + 8 * math.sin(angle)
        x2 = cx + 10 * math.cos(angle)
        y2 = cy + 10 * math.sin(angle)
        p.drawLine(int(x1), int(y1), int(x2), int(y2))


def _draw_signal(p, cx, cy, c):
    """Comm Center: signal / antenna icon."""
    p.setPen(QPen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Vertical antenna mast
    p.drawLine(int(cx), int(cy + 8), int(cx), int(cy - 4))
    # Signal arcs (3 concentric)
    for i, r in enumerate([5, 9, 13]):
        p.drawArc(int(cx - r), int(cy - 4 - r), r * 2, r * 2,
                  30 * 16, 120 * 16)
    # Base dot
    p.setBrush(c)
    p.drawEllipse(int(cx - 2), int(cy - 6), 4, 4)


# ─── Icon Tab Button ────────────────────────────────────────────────────────

class _IconTabButton(QPushButton):
    """A nav tab button that renders its icon with QPainter."""

    def __init__(self, icon_fn, label: str, index: int, parent=None):
        super().__init__(parent)
        self.setProperty("class", "NavTab")
        self.setFlat(True)
        self.setStyleSheet("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_fn = icon_fn
        self._label = label
        self._active = False
        self._hover = False

        # Calculate width from text
        self.setMinimumWidth(110)
        self.setFixedHeight(44)

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._on_theme)

    def _on_theme(self, _):
        self.update()

    def setActive(self, active: bool):
        self._active = active
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        # Draw the QSS base (background, border-bottom indicator)
        super().paintEvent(event)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        tm = ThemeManager.instance()
        if self._active:
            icon_color = QColor(tm.color("accent_blue"))
            text_color = QColor(tm.color("fg_primary"))
        elif self._hover:
            icon_color = QColor(tm.color("fg_secondary"))
            text_color = QColor(tm.color("fg_secondary"))
        else:
            icon_color = QColor(tm.color("fg_muted"))
            text_color = QColor(tm.color("fg_muted"))

        w, h = self.width(), self.height()

        # Icon on left side
        icon_cx = 18
        icon_cy = h // 2 - 2

        self._icon_fn(p, icon_cx, icon_cy, icon_color)

        # Label text
        p.setPen(text_color)
        weight = QFont.Weight.DemiBold if self._active else QFont.Weight.Medium
        font = QFont("Inter", 13, weight)
        p.setFont(font)
        text_rect = self.rect().adjusted(34, 2, -4, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._label)
        p.end()


# ─── NavBar ────────────────────────────────────────────────────────────────

class NavBar(QWidget):
    tab_changed = pyqtSignal(int)

    TABS = [
        ("Pre-Op",       _draw_clipboard),
        ("Live Video",   _draw_camera),
        ("Live Control", _draw_joystick),
        ("Settings",     _draw_gear),
        ("Comm Center",  _draw_signal),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavBar")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(4)

        self.buttons: list[_IconTabButton] = []
        for i, (name, icon_fn) in enumerate(self.TABS):
            btn = _IconTabButton(icon_fn, name, i)
            btn.clicked.connect(lambda checked, idx=i: self.set_active(idx))
            layout.addWidget(btn)
            self.buttons.append(btn)
        layout.addStretch()

        self.set_active(0)

    def set_active(self, idx: int):
        for i, btn in enumerate(self.buttons):
            btn.setActive(i == idx)
        self.tab_changed.emit(idx)
