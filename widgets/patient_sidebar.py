"""
Patient Sidebar — Redesigned for medical-grade ultrawide layout.
Sections: Patient Info (with Medication, Diagnosis), Procedure Info (with Surgery Notes),
Patient Vitals, System Status.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                             QSizePolicy, QProgressBar, QGridLayout, QTextEdit,
                             QScrollArea)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor


def hline():
    f = QFrame()
    f.setObjectName("HLine")
    f.setFrameShape(QFrame.Shape.HLine)
    return f


def field_row(label, value, bold=False, warn=False):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    lbl.setObjectName("FieldLabel")
    val = QLabel(value)
    val.setObjectName("FieldValueWarn" if warn else ("FieldValueBold" if bold else "FieldValue"))
    val.setAlignment(Qt.AlignmentFlag.AlignRight)
    row.addWidget(lbl)
    row.addStretch()
    row.addWidget(val)
    return row


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


class SidebarCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarCard")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 10, 14, 10)
        self.layout.setSpacing(6)
        t = QLabel(title.upper())
        t.setObjectName("SidebarSectionTitle")
        self.layout.addWidget(t)

    def add_row(self, label, value, bold=False, warn=False):
        self.layout.addLayout(field_row(label, value, bold, warn))

    def add_widget(self, w):
        self.layout.addWidget(w)


class PatientSidebar(QWidget):
    """Left sidebar: Patient Information + Procedure Information + Patient Vitals + System Status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(290)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)

        # --- Patient Information card ---
        patient_card = SidebarCard("Patient Information")
        header = QHBoxLayout()
        avatar = QLabel("MK")
        avatar.setObjectName("PatientAvatar")
        avatar.setFixedSize(42, 42)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(avatar)
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name = QLabel("Marisa K\u00F6hler")
        name.setObjectName("PatientName")
        meta = QLabel("MRN  \u00B7  0048-23119")
        meta.setObjectName("PatientMeta")
        name_box.addWidget(name)
        name_box.addWidget(meta)
        header.addLayout(name_box)
        header.addStretch()
        patient_card.layout.addLayout(header)
        patient_card.layout.addWidget(hline())
        patient_card.add_row("Age / Sex", "58  \u00B7  F")
        patient_card.add_row("Blood", "O+")
        patient_card.add_row("Allergies", "Penicillin", warn=True)
        patient_card.add_row("Medication", "Propofol, Fentanyl")
        patient_card.add_row("Diagnosis", "Acute Cholecystitis", bold=True)
        layout.addWidget(patient_card)

        # --- Procedure Information card ---
        proc_card = SidebarCard("Procedure Information")
        type_row = QVBoxLayout()
        type_row.setSpacing(3)
        type_lbl = QLabel("Type")
        type_lbl.setObjectName("FieldLabel")
        type_val = QLabel("Laparoscopic Cholecystectomy")
        type_val.setObjectName("FieldValueBold")
        type_val.setStyleSheet("font-size: 15px;")
        type_val.setWordWrap(True)
        type_row.addWidget(type_lbl)
        type_row.addWidget(type_val)
        proc_card.layout.addLayout(type_row)
        proc_card.layout.addWidget(hline())
        proc_card.add_row("Code", "ICD-10 K80.20")
        proc_card.add_row("Surgeon", "Dr. A. Voss", bold=True)
        proc_card.add_row("Assist", "Dr. R. Iyer", bold=True)
        proc_card.add_row("OR", "Suite 03")
        proc_card.add_row("Start", "08:42 UTC")
        proc_card.layout.addWidget(hline())

        phase_lbl = QLabel("PHASE")
        phase_lbl.setObjectName("SidebarCardTitle")
        proc_card.layout.addWidget(phase_lbl)
        phase_row = QHBoxLayout()
        phase_name = QLabel("Dissection")
        phase_name.setObjectName("FieldValueBold")
        phase_step = QLabel("Step 4 / 7")
        phase_step.setObjectName("FieldLabel")
        phase_row.addWidget(phase_name)
        phase_row.addStretch()
        phase_row.addWidget(phase_step)
        proc_card.layout.addLayout(phase_row)

        progress = QProgressBar()
        progress.setRange(0, 7)
        progress.setValue(4)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        proc_card.layout.addWidget(progress)
        proc_card.layout.addWidget(hline())

        # Surgery Notes
        notes_lbl = QLabel("PROCEDURE NOTES")
        notes_lbl.setObjectName("SidebarCardTitle")
        proc_card.layout.addWidget(notes_lbl)
        self.surgery_notes = QTextEdit()
        self.surgery_notes.setObjectName("SurgeryNotes")
        self.surgery_notes.setPlainText(
            "Patient positioned.\n"
            "Access achieved.\n"
            "Tumor identified.\n"
            "Blood loss minimal."
        )
        self.surgery_notes.setMinimumHeight(100)
        self.surgery_notes.setMaximumHeight(140)
        proc_card.layout.addWidget(self.surgery_notes)
        layout.addWidget(proc_card)

        # --- Patient Vitals card ---
        vitals_card = SidebarCard("Patient Vitals")
        vitals_grid = QGridLayout()
        vitals_grid.setSpacing(8)
        vitals_data = [
            ("HR", "74", "bpm", "#10B981", 0, 0),
            ("SpO\u2082", "98", "%", "#10B981", 0, 1),
            ("BP", "118/74", "mmHg", "#F5F7FA", 1, 0),
            ("Temp", "36.8", "\u00B0C", "#F5F7FA", 1, 1),
        ]
        for label, value, unit, val_color, row_idx, col_idx in vitals_data:
            cell = QFrame()
            cell.setObjectName("Card")
            cell_lay = QVBoxLayout(cell)
            cell_lay.setContentsMargins(10, 8, 10, 8)
            cell_lay.setSpacing(2)
            lab = QLabel(label)
            lab.setObjectName("VitalLabel")
            val = QLabel(value)
            val.setObjectName("VitalValue")
            val.setStyleSheet(f"font-size: 28px; color: {val_color};")
            un = QLabel(unit)
            un.setObjectName("VitalUnit")
            cell_lay.addWidget(lab)
            val_row = QHBoxLayout()
            val_row.setSpacing(4)
            val_row.addWidget(val)
            val_row.addWidget(un, alignment=Qt.AlignmentFlag.AlignBottom)
            val_row.addStretch()
            cell_lay.addLayout(val_row)
            vitals_grid.addWidget(cell, row_idx, col_idx)
        vitals_card.layout.addLayout(vitals_grid)
        layout.addWidget(vitals_card)

        # --- System Status card ---
        status_card = SidebarCard("System Status")
        status_items = [
            ("Manipulator", "Connected", "#10B981"),
            ("Vision", "30 FPS", "#10B981"),
            ("YOLO", "Running", "#10B981"),
            ("Recording", "OFF", "#6B7B8D"),
            ("Network", "Stable", "#10B981"),
            ("Storage", "82%", "#F5F7FA"),
        ]
        for label, value, color in status_items:
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            lbl = QLabel(label)
            lbl.setObjectName("SystemStatusLabel")
            val_row = QHBoxLayout()
            val_row.setSpacing(6)
            if color == "#10B981":
                dot = _StatusDot(color)
                val_row.addWidget(dot)
            val = QLabel(value)
            val.setObjectName("SystemStatusValue")
            val.setStyleSheet(f"color: {color};")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            val_row.addWidget(val)
            row.addWidget(lbl)
            row.addStretch()
            row.addLayout(val_row)
            status_card.layout.addLayout(row)
        layout.addWidget(status_card)

        layout.addStretch()

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
