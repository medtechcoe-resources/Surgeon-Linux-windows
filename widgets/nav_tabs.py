"""
Navigation tab bar — each tab has a QPainter-drawn icon + label.
No emojis. Icons are 18×18 px rendered inline.
Reduced to 4 tabs: Pre-Op, Live Video, Live Control, Settings.
"""
import math
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QPropertyAnimation, QEasingCurve, QSize
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF, QFontMetrics

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


def _draw_users(p, cx, cy, c):
    """User Management: users / profile silhouettes."""
    p.setPen(QPen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Primary user (center-left)
    p.drawEllipse(int(cx - 5), int(cy - 8), 6, 6)
    p.drawArc(int(cx - 9), int(cy - 1), 14, 14, 0 * 16, 180 * 16)
    # Secondary user (back-right)
    p.drawEllipse(int(cx + 2), int(cy - 7), 5, 5)
    p.drawArc(int(cx - 1), int(cy), 12, 12, 20 * 16, 140 * 16)


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
        self._index = index
        self._active = False
        self._hover = False

        # Calculate comfortable width dynamically from text + icon + padding
        fm = QFontMetrics(QFont("Inter", 13, QFont.Weight.DemiBold))
        text_width = fm.horizontalAdvance(self._label)
        calculated_w = 42 + text_width + 20
        self.setMinimumWidth(max(130, calculated_w))
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._on_theme)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(QFont("Inter", 13, QFont.Weight.DemiBold))
        text_width = fm.horizontalAdvance(self._label)
        calculated_w = 42 + text_width + 20
        return QSize(max(130, calculated_w), 44)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

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

        # Icon on left side with comfortable positioning
        icon_cx = 20
        icon_cy = h // 2 - 2

        self._icon_fn(p, icon_cx, icon_cy, icon_color)

        # Label text with ample padding to avoid clipping
        p.setPen(text_color)
        weight = QFont.Weight.DemiBold if self._active else QFont.Weight.Medium
        font = QFont("Inter", 13, weight)
        p.setFont(font)
        text_rect = self.rect().adjusted(38, 2, -8, 0)
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

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(24, 0, 24, 0)
        self._layout.setSpacing(12)

        self.buttons: list[_IconTabButton] = []
        for i, (name, icon_fn) in enumerate(self.TABS):
            btn = _IconTabButton(icon_fn, name, i)
            btn.clicked.connect(lambda checked, idx=i: self.set_active(idx))
            self._layout.addWidget(btn)
            self.buttons.append(btn)
        self._layout.addStretch()

        self.set_active(0)

    def add_tab(self, name: str, icon_fn) -> int:
        """Dynamically append a tab before the stretch.

        Idempotent: if a tab with the same label already exists, returns its index.
        """
        for btn in self.buttons:
            if btn._label == name:
                return btn._index

        idx = len(self.buttons)
        btn = _IconTabButton(icon_fn, name, idx)
        btn.clicked.connect(lambda checked, i=idx: self.set_active(i))
        # Insert before the stretch item (which is at layout index idx)
        self._layout.insertWidget(idx, btn)
        self.buttons.append(btn)
        return idx

    def set_active(self, idx: int):
        for i, btn in enumerate(self.buttons):
            btn.setActive(i == idx)
        self.tab_changed.emit(idx)
