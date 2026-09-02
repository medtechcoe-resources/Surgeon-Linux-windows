"""
Live Control Tab — 3-column layout.
Left  (~30%): Manipulator Telemetry (connected to live Data Generator telemetry)
Center (~40%): Interactive 3DOF Robot Simulator
Right  (~30%): Joint Controls + Robot Controls + System Health + Active Alerts panels
"""
import math
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QFrame, QProgressBar, QSizePolicy,
                             QPushButton, QSlider, QFileDialog)
from PyQt6.QtCore import Qt, QTimer, QPointF
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

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

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
    v = QLabel(str(value))
    v.setObjectName("TelemetryValue")
    u = QLabel(unit)
    u.setObjectName("TelemetryUnit")
    u.setStyleSheet("padding-top:6px;")
    row.addWidget(v)
    row.addWidget(u)
    row.addStretch()
    lay.addWidget(l)
    lay.addLayout(row)
    return box, v


# ═══════════════════════════════════════════════════════════════════
#  JOINT ROW (limit monitoring)
# ═══════════════════════════════════════════════════════════════════

class JointRow(QWidget):
    def __init__(self, code, name, value, vmax, status="OK", level="good"):
        super().__init__()
        self._vmax = vmax
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

        self._bar = QProgressBar()
        self._bar.setRange(0, vmax)
        self._bar.setValue(int(abs(value)))
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        if level != "good":
            self._bar.setProperty("level", level)
        layout.addWidget(self._bar, 1)

        self._val_lbl = QLabel(f"{value:.1f}° / {vmax}°")
        self._val_lbl.setStyleSheet(
            "color:#B5BEC8; font-size:12px; "
            "font-family:'JetBrains Mono','Consolas',monospace;"
        )
        self._val_lbl.setFixedWidth(90)
        layout.addWidget(self._val_lbl)

        color_map = {"good": "#10B981", "caution": "#F59E0B", "critical": "#EF4444"}
        color = color_map.get(level, "#10B981")
        self._dot = _StatusDot(color)
        layout.addWidget(self._dot)

        self._stat_lbl = QLabel(status)
        self._stat_lbl.setStyleSheet(f"color:{color}; font-size:11px; font-weight:700;")
        self._stat_lbl.setFixedWidth(64)
        layout.addWidget(self._stat_lbl)

    def update_value(self, angle: float, status: str = None, level: str = None):
        """Update joint angle and visual indicators."""
        abs_angle = abs(angle)
        self._bar.setValue(min(int(abs_angle), self._vmax))
        self._val_lbl.setText(f"{angle:.1f}° / {self._vmax}°")

        if level is None:
            ratio = abs_angle / max(self._vmax, 1)
            if ratio > 0.85:
                level = "caution"
                status = status or "CAUTION"
            else:
                level = "good"
                status = status or "OK"

        color_map = {"good": "#10B981", "caution": "#F59E0B", "critical": "#EF4444"}
        color = color_map.get(level, "#10B981")
        self._dot.set_color(color)
        self._stat_lbl.setText(status or "OK")
        self._stat_lbl.setStyleSheet(f"color:{color}; font-size:11px; font-weight:700;")


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
        if 0 <= joint < len(self._angles):
            self._angles[joint] = deg
            self.update()

    def set_angles(self, j1: float, j2: float, j3: float):
        if self._motion_locked:
            return
        self._angles = [j1, j2, j3]
        self.update()

    def set_motion_lock(self, locked: bool):
        self._motion_locked = locked
        self.update()

    def go_home(self):
        self._angles = [0.0, 0.0, 0.0]
        self.update()

    # ── Kinematics ────────────────────────────────────────────────

    def _forward_kinematics(self):
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
            glow = QColor(joint_color)
            glow.setAlpha(40)
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(x - r - 4), int(y - r - 4),
                          (r + 4) * 2, (r + 4) * 2)

            p.setBrush(QColor(tm.color("bg_card")))
            p.setPen(QPen(joint_color, 2.5))
            p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)

            p.setPen(QColor(tm.color("fg_primary")))
            f = QFont("Inter", 8, QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(int(x - r), int(y - r), r * 2, r * 2,
                       Qt.AlignmentFlag.AlignCenter, labels[i])

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

    def set_value(self, v: int):
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self._val_lbl.setText(f"{v}{self._unit}")

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
        self._start_time = datetime.now()

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
        self._card_uptime = MetricCard("Session Uptime", "00:00:00", accent="cyan")
        self._card_control_mode = MetricCard("Control Mode", "TELEOP", sub="Scaling 3:1", accent="cyan")
        self._card_tip_force = MetricCard("Tip Force", "0.0", "N", "Limit 8.0 N", accent="cyan")
        cards_row.addWidget(self._card_uptime)
        cards_row.addWidget(self._card_control_mode)
        cards_row.addWidget(self._card_tip_force)
        left.addLayout(cards_row)

        # Manipulator Telemetry panel
        self._live_dot = _StatusDot("#F59E0B")
        self._live_label = QLabel("WAITING FOR DATA")
        self._live_label.setObjectName("StatusCaution")
        live_row = QWidget()
        lr = QHBoxLayout(live_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(6)
        lr.addWidget(self._live_dot)
        lr.addWidget(self._live_label)
        telem = PanelFrame("Manipulator Telemetry", right_widget=live_row)

        coords = QGridLayout()
        coords.setSpacing(10)
        coord_defs = [
            ("X", "0.00", "mm"), ("Y", "0.00", "mm"), ("Z", "0.00", "mm"),
            ("RX", "0.000", "rad"), ("RY", "0.000", "rad"), ("RZ", "0.000", "rad"),
        ]
        self._coord_labels = {}
        for i, (lab, val, unit) in enumerate(coord_defs):
            box, val_lbl = coord_box(lab, val, unit)
            self._coord_labels[lab] = val_lbl
            coords.addWidget(box, 0, i)
        telem.add_layout(coords)

        joint_title = QLabel("JOINT LIMIT MONITORING")
        joint_title.setObjectName("SectionTitle")
        telem.add_widget(joint_title)

        joint_defs = [
            ("J1", "Base Yaw",    0, 180, "OK", "good"),
            ("J2", "Shoulder",    0, 120, "OK", "good"),
            ("J3", "Elbow",       0, 170, "OK", "good"),
            ("J4", "Wrist Pitch", 0, 120, "OK", "good"),
            ("J5", "Wrist Roll",  0, 170, "OK", "good"),
            ("J6", "Tool Flange", 0, 120, "OK", "good"),
        ]
        self._joint_rows = {}
        for code, name, val, vmax, status, level in joint_defs:
            jrow = JointRow(code, name, val, vmax, status, level)
            self._joint_rows[code] = jrow
            telem.add_widget(jrow)

        footer = QHBoxLayout()
        self._footer_labels = {}
        for lab, val in [
            ("Motion State", "IDLE"),
            ("Servo Status", "NOMINAL"),
            ("Torque Status", "NOMINAL"),
            ("Scaling",      "3.00 : 1.00"),
        ]:
            box = QVBoxLayout()
            box.setSpacing(2)
            l = QLabel(lab)
            l.setObjectName("FieldLabel")
            v = QLabel(val)
            v.setObjectName("FieldValueBold")
            self._footer_labels[lab] = v
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
        self._stat_j_vals = {}
        for code, lab in [("J1", "Base Rot"), ("J2", "Shoulder"), ("J3", "Elbow")]:
            cell = QVBoxLayout()
            cell.setSpacing(2)
            l = QLabel(f"{code} ({lab})")
            l.setObjectName("FieldLabel")
            v = QLabel("0°")
            v.setObjectName("FieldValueBold")
            self._stat_j_vals[code] = v
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
        self._health_labels = {}
        for lab, val, color in [
            ("CPU",        "0%",          "#10B981"),
            ("Latency",    "0.0 ms",      "#10B981"),
            ("Robot Mode", "ACTIVE",      "#E6EDF3"),
            ("Safety Link","ESTABLISHED", "#10B981"),
        ]:
            row = QHBoxLayout()
            l = QLabel(lab)
            l.setObjectName("FieldLabel")
            v = QLabel(val)
            v.setStyleSheet(f"color:{color}; font-size:14px; font-weight:700;")
            self._health_labels[lab] = v
            row.addWidget(l)
            row.addStretch()
            row.addWidget(v)
            health.add_layout(row)
        health.add_stretch()
        right.addWidget(health)

        # ─ Active Alerts panel ─
        self._alerts_panel = PanelFrame("Active Alerts")
        self._alerts_layout = QVBoxLayout()
        self._alerts_layout.setSpacing(6)
        self._alerts_panel.add_layout(self._alerts_layout)
        self._alerts_panel.add_stretch()
        right.addWidget(self._alerts_panel)

        right.addStretch()
        outer.addLayout(right, 25)

        # Uptime clock timer
        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(1000)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start()

    def _update_uptime(self):
        delta = datetime.now() - self._start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        self._card_uptime.set_value(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    # ── Live Telemetry Updates (from Data Generator via Broker) ───

    def update_telemetry(self, payload: dict):
        """Update UI fields with real incoming robot telemetry."""
        if not payload or not isinstance(payload, dict):
            return

        # 1. Update Connection indicator
        self._live_dot.set_color("#10B981")
        self._live_label.setText("LIVE")
        self._live_label.setStyleSheet("color: #10B981; font-weight: bold;")

        # 2. Coordinates
        tool_pos = payload.get("tool_position", {})
        if "x" in tool_pos and "X" in self._coord_labels:
            self._coord_labels["X"].setText(f"{float(tool_pos['x']):.2f}")
        if "y" in tool_pos and "Y" in self._coord_labels:
            self._coord_labels["Y"].setText(f"{float(tool_pos['y']):.2f}")
        if "z" in tool_pos and "Z" in self._coord_labels:
            self._coord_labels["Z"].setText(f"{float(tool_pos['z']):.2f}")

        ee_rot = payload.get("end_effector_rotation", 0.0)
        if "RX" in self._coord_labels:
            self._coord_labels["RX"].setText(f"{ee_rot:.3f}")

        # 3. Joint Angles
        joint_angles = payload.get("joint_angles", {})
        for i in range(1, 7):
            key = f"j{i}"
            code = f"J{i}"
            if key in joint_angles and code in self._joint_rows:
                angle_val = float(joint_angles[key])
                self._joint_rows[code].update_value(angle_val)

        # 4. Robot Simulator (3DOF: J1, J2, J3)
        if "j1" in joint_angles and "j2" in joint_angles and "j3" in joint_angles:
            j1 = float(joint_angles["j1"])
            j2 = float(joint_angles["j2"])
            j3 = float(joint_angles["j3"])
            self.robot.set_angles(j1, j2, j3)
            if "J1" in self._stat_j_vals:
                self._stat_j_vals["J1"].setText(f"{int(j1)}°")
            if "J2" in self._stat_j_vals:
                self._stat_j_vals["J2"].setText(f"{int(j2)}°")
            if "J3" in self._stat_j_vals:
                self._stat_j_vals["J3"].setText(f"{int(j3)}°")

        # 5. Metrics & Status
        if "force" in payload:
            self._card_tip_force.set_value(f"{float(payload['force']):.1f}")

        if "motion_state" in payload and "Motion State" in self._footer_labels:
            self._footer_labels["Motion State"].setText(str(payload["motion_state"]))

        if "servo_status" in payload and "Servo Status" in self._footer_labels:
            self._footer_labels["Servo Status"].setText(str(payload["servo_status"]))

        if "torque_status" in payload and "Torque Status" in self._footer_labels:
            self._footer_labels["Torque Status"].setText(str(payload["torque_status"]))

        # 6. System Health
        if "cpu_usage" in payload and "CPU" in self._health_labels:
            self._health_labels["CPU"].setText(f"{float(payload['cpu_usage']):.1f}%")

        if "latency" in payload and "Latency" in self._health_labels:
            self._health_labels["Latency"].setText(f"{float(payload['latency']):.2f} ms")

        if "robot_status" in payload and "Robot Mode" in self._health_labels:
            self._health_labels["Robot Mode"].setText(str(payload["robot_status"]))

    def update_alerts(self, alert: dict):
        """Add newly arrived alert to the Active Alerts list."""
        if not alert or not isinstance(alert, dict):
            return

        sev = alert.get("severity", "INFO").upper()
        msg = alert.get("message", "System notification")
        ts = alert.get("timestamp", datetime.now().strftime("%H:%M:%S"))

        sev_colors = {
            "CRITICAL": "#EF4444",
            "WARNING": "#F59E0B",
            "INFO": "#0095FF",
        }
        color = sev_colors.get(sev, "#10B981")

        row = QHBoxLayout()
        dot = _StatusDot(color)
        row.addWidget(dot)
        row.addSpacing(8)

        m = QLabel(msg)
        m.setStyleSheet("font-size:13px; font-weight:500; color:#E6EDF3;")
        row.addWidget(m, 1)

        t = QLabel(ts.split(" ")[-1] if " " in ts else ts)
        t.setObjectName("FieldLabel")
        row.addWidget(t)

        self._alerts_layout.insertLayout(0, row)

        # Keep max 10 visible alerts in UI
        while self._alerts_layout.count() > 10:
            item = self._alerts_layout.takeAt(self._alerts_layout.count() - 1)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()

    def set_telemetry_disconnected(self):
        """Set telemetry state to disconnected/stale."""
        self._live_dot.set_color("#EF4444")
        self._live_label.setText("DISCONNECTED")
        self._live_label.setStyleSheet("color: #EF4444; font-weight: bold;")

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
            with open(path, "r", encoding="utf-8") as f:
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
            log.info(f"Loaded joint config from {path}: J1={j1}, J2={j2}, J3={j3}")
        except Exception as e:
            log.error(f"Failed to load JSON joint config from {path}: {e}")
