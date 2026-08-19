"""
Live Control Tab — 3-column redesign.
Left  (~30%): Manipulator Telemetry (preserved)
Center (~40%): Interactive 3DOF Robot Simulator
Right  (~30%): Joint Controls + Robot Controls panels
"""
import math
import json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QFrame, QProgressBar, QSizePolicy,
                             QPushButton, QSlider, QFileDialog)
from PyQt6.QtCore import (Qt, QTimer, QPropertyAnimation, QEasingCurve,
                          pyqtProperty, QPointF)
from PyQt6.QtGui import (QPainter, QColor, QPen, QFont, QLinearGradient,
                          QPolygonF)
from widgets.card import MetricCard, PanelFrame
from theme_manager import ThemeManager


# ═══════════════════════════════════════════════════════════════════
#  STATUS DOT
# ═══════════════════════════════════════════════════════════════════

class _StatusDot(QWidget):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(10, 10)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 8, 8)
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  COORD BOX
# ═══════════════════════════════════════════════════════════════════

def coord_box(label, value, unit):
    box = QFrame()
    box.setObjectName("Card")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(6)
    l = QLabel(label)
    l.setObjectName("TelemetryLabel")
    row = QHBoxLayout()
    v = QLabel(value)
    v.setObjectName("TelemetryValue")
    u = QLabel(unit)
    u.setObjectName("TelemetryUnit")
    u.setStyleSheet("padding-top:6px;")
    row.addWidget(v)
    row.addWidget(u)
    row.addStretch()
    lay.addWidget(l)
    lay.addLayout(row)
    return box


# ═══════════════════════════════════════════════════════════════════
#  JOINT ROW (limit monitoring)
# ═══════════════════════════════════════════════════════════════════

class JointRow(QWidget):
    def __init__(self, code, name, value, vmax, status="OK", level="good"):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(12)

        code_lbl = QLabel(code)
        code_lbl.setStyleSheet("color:#6B7B8D; font-size:13px; font-weight:700;")
        code_lbl.setFixedWidth(28)
        layout.addWidget(code_lbl)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("color:#E6EDF3; font-size:13px; font-weight:600;")
        name_lbl.setFixedWidth(110)
        layout.addWidget(name_lbl)

        bar = QProgressBar()
        bar.setRange(0, vmax)
        bar.setValue(value)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        if level != "good":
            bar.setProperty("level", level)
        layout.addWidget(bar, 1)

        val_lbl = QLabel(f"{value}° / {vmax}°")
        val_lbl.setStyleSheet(
            "color:#B5BEC8; font-size:12px; "
            "font-family:'JetBrains Mono','Consolas',monospace;"
        )
        val_lbl.setFixedWidth(90)
        layout.addWidget(val_lbl)

        color = {"good": "#10B981", "caution": "#F59E0B", "critical": "#EF4444"}[level]
        dot = _StatusDot(color)
        layout.addWidget(dot)
        stat_lbl = QLabel(status)
        stat_lbl.setStyleSheet(f"color:{color}; font-size:11px; font-weight:700;")
        stat_lbl.setFixedWidth(64)
        layout.addWidget(stat_lbl)


# ═══════════════════════════════════════════════════════════════════
#  3DOF ROBOT SIMULATOR
# ═══════════════════════════════════════════════════════════════════

class RobotSimulator(QFrame):
    """
    Interactive 2D 3-DOF planar robot arm visualizer.
    Joints: J1 (base rotation), J2 (shoulder pitch), J3 (elbow pitch).
    Forward kinematics rendered with QPainter.
    """

    LINK_LENGTHS = [110, 90, 70]   # pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumSize(300, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Joint angles in degrees (J1, J2, J3)
        self._angles = [0.0, 0.0, 0.0]
        self._motion_locked = False

        tm = ThemeManager.instance()
        tm.theme_changed.connect(lambda _: self.update())

    # ── Public API ────────────────────────────────────────────────

    def set_angle(self, joint: int, deg: float):
        """Set joint angle (0-indexed). Ignored when motion is locked."""
        if self._motion_locked:
            return
        self._angles[joint] = deg
        self.update()

    def set_motion_lock(self, locked: bool):
        self._motion_locked = locked
        self.update()

    def go_home(self):
        self._angles = [0.0, 0.0, 0.0]
        self.update()

    # ── Kinematics ────────────────────────────────────────────────

    def _forward_kinematics(self):
        """
        Returns list of (x, y) joint positions starting from base.
        Coordinate system: base at bottom-center, y-up.
        Widget coords: y-down, converted at draw time.
        """
        w, h = self.width(), self.height()
        base_x = w / 2
        base_y = h - 60          # base at bottom

        points = [(base_x, base_y)]
        cum_angle = 0.0

        for i, (angle, length) in enumerate(zip(self._angles, self.LINK_LENGTHS)):
            cum_angle += angle
            rad = math.radians(cum_angle - 90)   # -90 so 0° means upward
            prev_x, prev_y = points[-1]
            nx = prev_x + length * math.cos(rad)
            ny = prev_y + length * math.sin(rad)
            points.append((nx, ny))

        return points

    # ── Paint ────────────────────────────────────────────────────

    def paintEvent(self, event):
        tm = ThemeManager.instance()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background grid
        grid_color = QColor(tm.color("robot_grid"))
        p.setPen(QPen(grid_color, 1))
        spacing = 40
        for x in range(0, w, spacing):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, spacing):
            p.drawLine(0, y, w, y)

        # Forward kinematics
        pts = self._forward_kinematics()

        # Draw links
        link_color = QColor(tm.color("robot_link"))
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]

            # Gradient link
            grad = QLinearGradient(x1, y1, x2, y2)
            grad.setColorAt(0.0, link_color.lighter(120))
            grad.setColorAt(1.0, link_color)
            pen = QPen(link_color, 8, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Draw joint circles
        joint_color = QColor(tm.color("robot_joint"))
        radii = [14, 12, 10]
        labels = ["J1", "J2", "J3"]
        angle_labels = [f"{int(a)}°" for a in self._angles]

        for i, (x, y) in enumerate(pts[:-1]):
            r = radii[i]
            # Glow halo
            glow = QColor(joint_color)
            glow.setAlpha(40)
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(x - r - 4), int(y - r - 4),
                          (r + 4) * 2, (r + 4) * 2)

            # Joint body
            p.setBrush(QColor(tm.color("bg_card")))
            p.setPen(QPen(joint_color, 2.5))
            p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)

            # Joint label
            p.setPen(QColor(tm.color("fg_primary")))
            f = QFont("Inter", 8, QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(int(x - r), int(y - r), r * 2, r * 2,
                       Qt.AlignmentFlag.AlignCenter, labels[i])

            # Angle readout beside joint
            p.setPen(QColor(tm.color("robot_text")))
            f2 = QFont("JetBrains Mono", 9)
            p.setFont(f2)
            p.drawText(int(x + r + 6), int(y - 8), 50, 16,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       angle_labels[i])

        # End-effector tip
        ex, ey = pts[-1]
        tip_color = QColor(tm.color("robot_tip"))
        tip_size = 10
        tip_pts = QPolygonF([
            QPointF(ex, ey - tip_size),
            QPointF(ex + tip_size * 0.7, ey + tip_size * 0.5),
            QPointF(ex - tip_size * 0.7, ey + tip_size * 0.5),
        ])
        p.setBrush(tip_color)
        p.setPen(QPen(tip_color.lighter(140), 1.5))
        p.drawPolygon(tip_pts)

        # Base pedestal
        bx, by = pts[0]
        base_color = QColor(tm.color("border_heavy"))
        p.setBrush(base_color)
        p.setPen(QPen(base_color.lighter(130), 2))
        p.drawRoundedRect(int(bx - 22), int(by), 44, 12, 4, 4)
        p.drawRoundedRect(int(bx - 30), int(by + 12), 60, 8, 2, 2)

        # Lock indicator
        if self._motion_locked:
            p.setPen(QColor(tm.color("accent_red")))
            f3 = QFont("Inter", 11, QFont.Weight.Bold)
            p.setFont(f3)
            p.drawText(self.rect().adjusted(0, 10, 0, 0),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       "MOTION LOCKED")

        # Home label
        if all(abs(a) < 0.5 for a in self._angles):
            p.setPen(QColor(tm.color("fg_muted")))
            f4 = QFont("Inter", 10)
            p.setFont(f4)
            p.drawText(self.rect().adjusted(0, 10, 0, 0),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       "HOME POSITION")

        p.end()


# ═══════════════════════════════════════════════════════════════════
#  JOINT CONTROL SLIDER ROW
# ═══════════════════════════════════════════════════════════════════

class _JointSlider(QWidget):
    def __init__(self, code: str, name: str, min_val: int, max_val: int,
                 on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._unit = "°"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(4)

        header = QHBoxLayout()
        lbl = QLabel(f"{code}  {name}")
        lbl.setObjectName("SliderLabel")
        self._val_lbl = QLabel(f"0{self._unit}")
        self._val_lbl.setObjectName("SliderValue")
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(lbl)
        header.addStretch()
        header.addWidget(self._val_lbl)
        lay.addLayout(header)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(0)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._on_val)
        lay.addWidget(self.slider)

    def _on_val(self, v: int):
        self._val_lbl.setText(f"{v}{self._unit}")
        self._on_change(v)

    def set_enabled(self, enabled: bool):
        self.slider.setEnabled(enabled)

    def reset(self):
        self.slider.blockSignals(True)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self._val_lbl.setText(f"0{self._unit}")


# ═══════════════════════════════════════════════════════════════════
#  MAIN LIVE CONTROL SCREEN
# ═══════════════════════════════════════════════════════════════════

class LiveControlScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._motion_enabled = True

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 14, 0, 0)
        outer.setSpacing(14)

        # ══════════════════════════════════════════════
        #  LEFT COLUMN (~30%) — Manipulator Telemetry
        # ══════════════════════════════════════════════
        left = QVBoxLayout()
        left.setSpacing(12)

        # Top metric cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        cards_row.addWidget(MetricCard("Session Uptime", "00:42:18", accent="cyan"))
        cards_row.addWidget(MetricCard("Control Mode", "TELEOP", sub="Scaling 3:1", accent="cyan"))
        cards_row.addWidget(MetricCard("Tip Force", "2.4", "N", "Limit 8.0 N", accent="cyan"))
        left.addLayout(cards_row)

        # Manipulator Telemetry panel
        live_dot = _StatusDot("#10B981")
        live_label = QLabel("LIVE")
        live_label.setObjectName("StatusGood")
        live_row = QWidget()
        lr = QHBoxLayout(live_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(6)
        lr.addWidget(live_dot)
        lr.addWidget(live_label)
        telem = PanelFrame("Manipulator Telemetry", right_widget=live_row)

        coords = QGridLayout()
        coords.setSpacing(10)
        coord_data = [
            ("X", "128.42", "mm"), ("Y", "-22.18", "mm"), ("Z", "318.04", "mm"),
            ("RX", "1.482", "rad"), ("RY", "-0.215", "rad"), ("RZ", "0.731", "rad"),
        ]
        for i, (lab, val, unit) in enumerate(coord_data):
            coords.addWidget(coord_box(lab, val, unit), 0, i)
        telem.add_layout(coords)

        joint_title = QLabel("JOINT LIMIT MONITORING")
        joint_title.setObjectName("SectionTitle")
        telem.add_widget(joint_title)

        joints = [
            ("J1", "Base Yaw",    28, 180, "OK",      "good"),
            ("J2", "Shoulder",    62, 120, "OK",      "good"),
            ("J3", "Elbow",       91, 145, "CAUTION", "caution"),
            ("J4", "Wrist Pitch", 18,  90, "OK",      "good"),
            ("J5", "Wrist Roll", 142, 270, "OK",      "good"),
            ("J6", "Tool Flange",  7, 360, "OK",      "good"),
        ]
        for code, name, val, vmax, status, level in joints:
            telem.add_widget(JointRow(code, name, val, vmax, status, level))

        footer = QHBoxLayout()
        for lab, val in [
            ("Vel Cmd",      "0.42 m/s"),
            ("Vel Act",      "0.41 m/s"),
            ("Tremor Filter","ON  ·  12 Hz"),
            ("Scaling",      "3.00 : 1.00"),
        ]:
            box = QVBoxLayout()
            box.setSpacing(2)
            l = QLabel(lab)
            l.setObjectName("FieldLabel")
            v = QLabel(val)
            v.setObjectName("FieldValueBold")
            box.addWidget(l)
            box.addWidget(v)
            footer.addLayout(box)
            footer.addStretch()
        telem.add_layout(footer)

        left.addWidget(telem, 1)
        outer.addLayout(left, 25)

        # ══════════════════════════════════════════════
        #  CENTER COLUMN (~40%) — 3DOF Robot Simulator
        # ══════════════════════════════════════════════
        center = QVBoxLayout()
        center.setSpacing(10)

        sim_header = QLabel("ROBOT VISUALIZATION")
        sim_header.setObjectName("SectionTitle")
        center.addWidget(sim_header)

        self.robot = RobotSimulator()
        self.robot.setMinimumSize(300, 500)
        center.addWidget(self.robot, 1)

        # Stat row under sim
        stat_row = QHBoxLayout()
        stat_row.setSpacing(12)
        for code, lab in [("J1", "Base Rot"), ("J2", "Shoulder"), ("J3", "Elbow")]:
            cell = QVBoxLayout()
            cell.setSpacing(2)
            l = QLabel(code)
            l.setObjectName("FieldLabel")
            v = QLabel("0°")
            v.setObjectName("FieldValueBold")
            cell.addWidget(l)
            cell.addWidget(v)
            stat_row.addLayout(cell)
            stat_row.addStretch()
        center.addLayout(stat_row)

        outer.addLayout(center, 50)

        # ══════════════════════════════════════════════
        #  RIGHT COLUMN (~30%) — Joint + Robot Controls
        # ══════════════════════════════════════════════
        right = QVBoxLayout()
        right.setSpacing(12)

        # ─ Joint Controls panel ─
        joint_ctrl = PanelFrame("Joint Controls")

        self._s_j1 = _JointSlider(
            "J1", "Base Rotation", -180, 180,
            lambda v: self.robot.set_angle(0, v)
        )
        self._s_j2 = _JointSlider(
            "J2", "Shoulder Pitch", -120, 120,
            lambda v: self.robot.set_angle(1, v)
        )
        self._s_j3 = _JointSlider(
            "J3", "Elbow Pitch", -120, 120,
            lambda v: self.robot.set_angle(2, v)
        )
        for s in (self._s_j1, self._s_j2, self._s_j3):
            joint_ctrl.add_widget(s)

        right.addWidget(joint_ctrl)

        # ─ Robot Controls panel ─
        robot_ctrl = PanelFrame("Robot Controls")

        self.btn_load_json = QPushButton("Load JSON Config")
        self.btn_load_json.setProperty("class", "SecondaryButton")
        self.btn_load_json.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_load_json.clicked.connect(self._load_json)
        robot_ctrl.add_widget(self.btn_load_json)

        self.btn_home = QPushButton("Home Position")
        self.btn_home.setProperty("class", "SecondaryButton")
        self.btn_home.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_home.clicked.connect(self._go_home)
        robot_ctrl.add_widget(self.btn_home)

        self.btn_motion = QPushButton("Motion: ENABLE")
        self.btn_motion.setProperty("class", "ToggleButton")
        self.btn_motion.setProperty("active", "true")
        self.btn_motion.setProperty("accent", "blue")
        self.btn_motion.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_motion.clicked.connect(self._toggle_motion)
        robot_ctrl.add_widget(self.btn_motion)

        right.addWidget(robot_ctrl)

        # ─ System Health panel ─
        health = PanelFrame("System Health")
        for lab, val, color in [
            ("CPU",        "38%",         "#10B981"),
            ("GPU 0",      "42%",         "#10B981"),
            ("RAM",        "14.2 / 64 GB","#E6EDF3"),
            ("Thermal",    "51 °C",       "#E6EDF3"),
            ("Storage I/O","184 MB/s",    "#E6EDF3"),
        ]:
            row = QHBoxLayout()
            l = QLabel(lab)
            l.setObjectName("FieldLabel")
            v = QLabel(val)
            v.setStyleSheet(f"color:{color}; font-size:14px; font-weight:700;")
            row.addWidget(l)
            row.addStretch()
            row.addWidget(v)
            health.add_layout(row)
        health.add_stretch()
        right.addWidget(health)

        # ─ Active Alerts panel ─
        alerts = PanelFrame("Active Alerts")
        alert_data = [
            ("#F59E0B", "J3 nearing limit",        "T+ 42:11"),
            ("#10B981", "Tool change verified",     "T+ 39:48"),
            ("#0095FF", "Phase advanced — Dissection", "T+ 36:02"),
        ]
        for color, msg, timestamp in alert_data:
            row = QHBoxLayout()
            dot = _StatusDot(color)
            row.addWidget(dot)
            row.addSpacing(8)
            m = QLabel(msg)
            m.setStyleSheet("font-size:13px; font-weight:500;")
            row.addWidget(m, 1)
            t = QLabel(timestamp)
            t.setObjectName("FieldLabel")
            row.addWidget(t)
            alerts.add_layout(row)
        alerts.add_stretch()
        right.addWidget(alerts)

        right.addStretch()
        outer.addLayout(right, 25)

    # ── Robot Control Handlers ─────────────────────────────────────

    def _go_home(self):
        for s in (self._s_j1, self._s_j2, self._s_j3):
            s.reset()
        self.robot.go_home()

    def _toggle_motion(self):
        self._motion_enabled = not self._motion_enabled
        enabled = self._motion_enabled

        # Update button state
        self.btn_motion.setProperty("active", "true" if enabled else "false")
        self.btn_motion.setProperty("accent", "blue" if enabled else "")
        self.btn_motion.setText(f"Motion: {'ENABLE' if enabled else 'DISABLE'}")
        self.btn_motion.style().unpolish(self.btn_motion)
        self.btn_motion.style().polish(self.btn_motion)

        # Lock/unlock robot and sliders
        self.robot.set_motion_lock(not enabled)
        for s in (self._s_j1, self._s_j2, self._s_j3):
            s.set_enabled(enabled)

    def _load_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Joint Config", "",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            j1 = float(data.get("j1", 0))
            j2 = float(data.get("j2", 0))
            j3 = float(data.get("j3", 0))
            # Clamp and apply
            j1 = max(-180, min(180, j1))
            j2 = max(-120, min(120, j2))
            j3 = max(-120, min(120, j3))
            self._s_j1.slider.setValue(int(j1))
            self._s_j2.slider.setValue(int(j2))
            self._s_j3.slider.setValue(int(j3))
        except Exception as e:
            pass   # Silently ignore malformed JSON in UI-only mode
