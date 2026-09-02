"""
Emergency Stop Button — Rectangular, high-visibility, theme-compatible.
Designed to sit inline in the header (top-right).
"""
from PyQt6.QtWidgets import QPushButton, QMessageBox
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QFont


class EmergencyStopButton(QPushButton):
    """
    Professional rectangular E-Stop button for placement in the header.
    Uses QPainter to render a stop-hexagon icon alongside the label text.
    Pulses (border brightness) to maintain attention without distraction.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EStop")
        self.setFixedSize(220, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._confirm)
        self.setText("")   # Text rendered by paintEvent

        # Pulse animation — alternates border alpha
        self._pulse = True
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._do_pulse)
        self._pulse_timer.start(900)

    # ── Pulse ─────────────────────────────────────────────────────────────

    def _do_pulse(self):
        self._pulse = not self._pulse
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        # Let QSS draw the base background/border
        super().paintEvent(event)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # ── Stop icon (octagon) ──
        cx, cy = 22, h // 2
        r = 10
        import math
        pts = []
        for i in range(8):
            angle = math.radians(22.5 + i * 45)
            pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))

        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF
        polygon = QPolygonF([QPointF(x, y) for x, y in pts])

        p.setBrush(QColor(255, 255, 255, 220))
        p.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
        p.drawPolygon(polygon)

        # Vertical bars inside octagon (pause/stop symbol)
        p.setPen(QPen(QColor("#EF4444"), 2.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(int(cx - 3), int(cy - 4), int(cx - 3), int(cy + 4))
        p.drawLine(int(cx + 3), int(cy - 4), int(cx + 3), int(cy + 4))

        # ── Label text ──
        p.setPen(QColor(255, 255, 255))
        font = QFont("Inter", 12, QFont.Weight.ExtraBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(font)
        text_rect = self.rect().adjusted(40, 0, -8, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "EMERGENCY STOP")

        p.end()

    # ── Confirm dialog ─────────────────────────────────────────────────────

    def _confirm(self):
        box = QMessageBox(self)
        box.setWindowTitle("Emergency Stop Activated")
        box.setText(
            "<b>Robot motion has been halted immediately.</b><br><br>"
            "All axes are locked. Manual reset is required before resuming operation."
        )
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()

    # ── Legacy compat (called by main.py resizeEvent) ─────────────────────

    def reposition(self):
        """No-op: button is now inline in the header, not floating."""
        pass
