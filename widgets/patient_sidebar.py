"""
Patient Sidebar — Redesigned for medical-grade ultrawide layout.
Sections: Patient Info (with Medication, Diagnosis), Procedure Info (with Surgery Notes),
Patient Vitals, System Status.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                             QSizePolicy, QProgressBar, QGridLayout, QTextEdit,
                             QScrollArea, QCheckBox, QMessageBox)
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


    def _confirm_site_marking(self, state):
        """Require explicit surgeon confirmation for surgical-site marking."""
        checkbox = self._readiness_checks["site"]

        if state == Qt.CheckState.Checked:
            result = QMessageBox.question(
                self,
                "Confirm Site Marking",
                "Confirm that the surgical site has been identified "
                "and verified.",
                QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )

            if result != QMessageBox.StandardButton.Yes:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
                self._update_readiness_ui()
                return

            self._readiness_status_labels["site"].setText("CONFIRMED")
            self._readiness_status_labels["site"].setStyleSheet(
                "color: #4ADE80; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )

        else:
            self._readiness_status_labels["site"].setText("CONFIRM")
            self._readiness_status_labels["site"].setStyleSheet(
                "color: #94A3B8; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )

        self._update_readiness_ui()

    def _confirm_lab_results(self, state):
        """Require explicit confirmation that required lab results were reviewed."""
        checkbox = self._readiness_checks["labs"]

        if state == Qt.CheckState.Checked:
            result = QMessageBox.question(
                self,
                "Review Lab Results",
                "Confirm that the required laboratory results "
                "have been reviewed and are available for the case.",
                QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )

            if result != QMessageBox.StandardButton.Yes:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

                self._readiness_status_labels["labs"].setText("REVIEW")
                self._readiness_status_labels["labs"].setStyleSheet(
                    "color: #FBBF24; font-size: 9px; font-weight: 700; "
                    "background: transparent;"
                )

                self._update_readiness_ui()
                return

            self._readiness_status_labels["labs"].setText("REVIEWED")
            self._readiness_status_labels["labs"].setStyleSheet(
                "color: #4ADE80; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )

        else:
            self._readiness_status_labels["labs"].setText("REVIEW")
            self._readiness_status_labels["labs"].setStyleSheet(
                "color: #FBBF24; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )

        self._update_readiness_ui()

    def _update_readiness_ui(self):
        """Update count, progress, and overall case-readiness state."""
        completed = sum(
            1
            for checkbox in self._readiness_checks.values()
            if checkbox.isChecked()
        )

        self._readiness_progress.setValue(completed)
        self._readiness_count.setText(
            f"{completed} / {self._readiness_total} COMPLETED"
        )

        if completed >= self._readiness_total:
            self._readiness_case_status.setText("CASE READY")
            self._readiness_case_status.setStyleSheet(
                "color: #4ADE80; font-size: 11px; font-weight: 800; "
                "background: transparent;"
            )
        else:
            remaining = self._readiness_total - completed
            self._readiness_case_status.setText(
                f"PRE-OP INCOMPLETE  •  {remaining} PENDING"
            )
            self._readiness_case_status.setStyleSheet(
                "color: #FBBF24; font-size: 11px; font-weight: 800; "
                "background: transparent;"
            )

    def set_preop_readiness(self, key, completed, status_text=None):
        """
        Allow future workflow/backend integration to update a readiness item.

        Example:
            sidebar.set_preop_readiness("labs", True, "AVAILABLE")
        """
        if key not in self._readiness_checks:
            return

        checkbox = self._readiness_checks[key]

        checkbox.blockSignals(True)
        checkbox.setChecked(bool(completed))
        checkbox.blockSignals(False)

        if status_text:
            self._readiness_status_labels[key].setText(str(status_text))

        if completed:
            self._readiness_status_labels[key].setStyleSheet(
                "color: #4ADE80; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )
        else:
            self._readiness_status_labels[key].setStyleSheet(
                "color: #FBBF24; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )

        self._update_readiness_ui()
    def __init__(self, parent=None):
        super().__init__(parent)

        # Keep the original Marisa Köhler sidebar width and card language.
        self.setFixedWidth(320)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)

        # ============================================================
        # 1. PATIENT INFORMATION
        # ============================================================
        patient_card = SidebarCard("Patient Information")

        header = QHBoxLayout()

        avatar = QLabel("MK")
        avatar.setObjectName("PatientAvatar")
        avatar.setFixedSize(42, 42)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header.addWidget(avatar)

        name_box = QVBoxLayout()
        name_box.setSpacing(2)

        name = QLabel("Marisa Köhler")
        name.setObjectName("PatientName")

        meta = QLabel("MRN  ·  0048-23119")
        meta.setObjectName("PatientMeta")

        name_box.addWidget(name)
        name_box.addWidget(meta)
        header.addLayout(name_box)
        header.addStretch()

        patient_card.layout.addLayout(header)
        patient_card.layout.addWidget(hline())

        # Keep this card focused only on identity/demographics.
        patient_card.add_row("Age / Sex", "58  ·  F")
        patient_card.add_row("Blood", "O+")

        layout.addWidget(patient_card)

        # ============================================================
        # 2. ALLERGIES
        #    Replaces the old Procedure Information card.
        # ============================================================
        allergy_card = SidebarCard("Allergies")

        allergy_title = QLabel("KNOWN ALLERGIES")
        allergy_title.setObjectName("SidebarCardTitle")
        allergy_card.layout.addWidget(allergy_title)

        allergy_row = QHBoxLayout()
        allergy_row.setSpacing(8)

        penicillin = QLabel("Penicillin")
        penicillin.setObjectName("FieldValueWarn")
        penicillin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        penicillin.setStyleSheet(
            "padding: 6px 10px; "
            "border-radius: 5px; "
            "background: rgba(239, 68, 68, 0.12); "
            "border: 1px solid rgba(239, 68, 68, 0.55);"
        )

        latex = QLabel("Latex")
        latex.setAlignment(Qt.AlignmentFlag.AlignCenter)
        latex.setStyleSheet(
            "padding: 6px 10px; "
            "border-radius: 5px; "
            "background: rgba(239, 68, 68, 0.12); "
            "border: 1px solid rgba(239, 68, 68, 0.55);"
        )

        allergy_row.addWidget(penicillin)
        allergy_row.addWidget(latex)
        allergy_row.addStretch()

        allergy_card.layout.addLayout(allergy_row)
        allergy_card.layout.addWidget(hline())
        allergy_card.add_row("Status", "Reviewed", bold=True)

        layout.addWidget(allergy_card)

        # ============================================================
        # 3. MEDICAL HISTORY
        #    Replaces the old Dissection / Phase information.
        # ============================================================
        history_card = SidebarCard("Medical History")

        history_card.add_row(
            "Diagnosis",
            "Acute Cholecystitis",
            bold=True,
        )

        history_card.layout.addWidget(hline())

        history_title = QLabel("CLINICAL NOTES")
        history_title.setObjectName("SidebarCardTitle")
        history_card.layout.addWidget(history_title)

        history_notes = QLabel(
            "Patient positioned.\n"
            "Access achieved.\n"
            "Tumor identified.\n"
            "Blood loss minimal."
        )
        history_notes.setWordWrap(True)
        history_notes.setObjectName("FieldValue")
        history_card.layout.addWidget(history_notes)

        layout.addWidget(history_card)

        # ============================================================
        # 4. ACTIVE MEDICATIONS
        #    Replaces the old Patient Vitals card.
        # ============================================================
        medication_card = SidebarCard("Active Medications")

        medication_card.add_row(
            "Current",
            "Propofol, Fentanyl",
            bold=True,
        )

        medication_card.layout.addWidget(hline())

        medication_note = QLabel(
            "Medication status verified for procedure."
        )
        medication_note.setWordWrap(True)
        medication_note.setObjectName("FieldLabel")
        medication_card.layout.addWidget(medication_note)

        layout.addWidget(medication_card)

        # ============================================================
        # 5. VITALS
        #    Replaces the old System Status card.
        #    Keep the existing dynamic PatientVitalsModel interface.
        # ============================================================
        vitals_card = SidebarCard("Vitals")

        v_title_row = QHBoxLayout()

        v_title = QLabel("PATIENT VITALS")
        v_title.setObjectName("SidebarSectionTitle")

        self._vitals_status_label = QLabel("NO DATA")
        self._vitals_status_label.setStyleSheet(
            "color: #94A3B8; "
            "font-size: 10px; "
            "font-weight: 700; "
            "padding: 2px 6px; "
            "border-radius: 4px; "
            "background: rgba(148, 163, 184, 0.15);"
        )

        v_title_row.addWidget(v_title)
        v_title_row.addStretch()
        v_title_row.addWidget(self._vitals_status_label)

        # Remove the SidebarCard's automatically-created title.
        if vitals_card.layout.count() > 0:
            old_title = vitals_card.layout.takeAt(0).widget()
            if old_title:
                old_title.deleteLater()

        vitals_card.layout.addLayout(v_title_row)

        vitals_grid = QGridLayout()
        vitals_grid.setSpacing(8)

        vitals_config = [
            ("HR", "bpm", 0, 0),
            ("SpO₂", "%", 0, 1),
            ("BP", "mmHg", 1, 0),
            ("Temp", "°C", 1, 1),
        ]

        self._vital_val_labels = {}

        for label_text, unit, row_idx, col_idx in vitals_config:
            cell = QFrame()
            cell.setObjectName("Card")

            cell_lay = QVBoxLayout(cell)
            cell_lay.setContentsMargins(10, 8, 10, 8)
            cell_lay.setSpacing(2)

            lab = QLabel(label_text)
            lab.setObjectName("VitalLabel")

            val = QLabel("--")
            val.setObjectName("VitalValue")
            val.setStyleSheet(
                "font-size: 26px; color: #94A3B8;"
            )

            key = "SpO2" if "SpO" in label_text else label_text
            self._vital_val_labels[key] = val

            un = QLabel(unit)
            un.setObjectName("VitalUnit")

            cell_lay.addWidget(lab)

            val_row = QHBoxLayout()
            val_row.setSpacing(4)
            val_row.addWidget(val)
            val_row.addWidget(
                un,
                alignment=Qt.AlignmentFlag.AlignBottom
            )
            val_row.addStretch()

            cell_lay.addLayout(val_row)

            vitals_grid.addWidget(
                cell,
                row_idx,
                col_idx,
            )

        vitals_card.layout.addLayout(vitals_grid)
        layout.addWidget(vitals_card)

        # System status is no longer displayed as a separate card.
        # Keep this dictionary so existing callers remain safe.
        self._system_status_dots = {}

        layout.addStretch(1)


        # --- Pre-Op Readiness card ---
        readiness_card = SidebarCard("Pre-Op Readiness")

        self._readiness_checks = {}
        self._readiness_status_labels = {}
        self._readiness_total = 5

        readiness_items = [
            ("consent", "Consent", "COMPLETED", True, False),
            ("imaging", "Imaging Review", "REVIEWED", True, False),
            ("anesthesia", "Anesthesia Clearance", "CLEARED", True, False),
            ("site", "Site Marking", "CONFIRM", False, True),
            ("labs", "Lab Results", "REVIEW", False, True),
        ]

        for key, label_text, status_text, checked, interactive in readiness_items:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            checkbox = QCheckBox()
            checkbox.setFixedWidth(20)
            checkbox.setChecked(checked)
            checkbox.setEnabled(interactive)

            checkbox.setStyleSheet("""
                QCheckBox {
                    spacing: 0px;
                    background: transparent;
                }
                QCheckBox::indicator {
                    width: 14px;
                    height: 14px;
                    border-radius: 3px;
                    border: 1px solid #475569;
                    background: #0F172A;
                }
                QCheckBox::indicator:checked {
                    border: 1px solid #38BDF8;
                    background: #38BDF8;
                }
                QCheckBox::indicator:disabled {
                    border: 1px solid #334155;
                    background: #172033;
                }
                QCheckBox::indicator:checked:disabled {
                    border: 1px solid #64748B;
                    background: #64748B;
                }
            """)

            name = QLabel(label_text)
            name.setObjectName("FieldValue")
            name.setStyleSheet(
                "font-size: 11px; font-weight: 600; background: transparent;"
            )

            status = QLabel(status_text)
            status.setAlignment(Qt.AlignmentFlag.AlignRight)
            status.setStyleSheet(
                "color: #94A3B8; font-size: 9px; font-weight: 700; "
                "background: transparent;"
            )

            if key in ("consent", "imaging", "anesthesia"):
                status.setStyleSheet(
                    "color: #4ADE80; font-size: 9px; font-weight: 700; "
                    "background: transparent;"
                )
            elif key == "labs":
                status.setStyleSheet(
                    "color: #FBBF24; font-size: 9px; font-weight: 700; "
                    "background: transparent;"
                )

            row.addWidget(checkbox)
            row.addWidget(name)
            row.addStretch()
            row.addWidget(status)

            readiness_card.layout.addLayout(row)

            self._readiness_checks[key] = checkbox
            self._readiness_status_labels[key] = status

        readiness_card.layout.addWidget(hline())

        readiness_heading = QLabel("CASE READINESS")
        readiness_heading.setObjectName("SidebarCardTitle")
        readiness_card.layout.addWidget(readiness_heading)

        readiness_status = QLabel("PRE-OP INCOMPLETE")
        readiness_status.setObjectName("FieldValueBold")
        readiness_status.setStyleSheet(
            "color: #FBBF24; font-size: 11px; font-weight: 800; "
            "background: transparent;"
        )
        self._readiness_case_status = readiness_status
        readiness_card.layout.addWidget(readiness_status)

        self._readiness_progress = QProgressBar()
        self._readiness_progress.setRange(0, self._readiness_total)
        self._readiness_progress.setValue(3)
        self._readiness_progress.setTextVisible(False)
        self._readiness_progress.setFixedHeight(6)
        self._readiness_progress.setStyleSheet("""
            QProgressBar {
                background: #172033;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: #38BDF8;
                border-radius: 3px;
            }
        """)
        readiness_card.layout.addWidget(self._readiness_progress)

        self._readiness_count = QLabel("3 / 5 COMPLETED")
        self._readiness_count.setStyleSheet(
            "color: #94A3B8; font-size: 9px; font-weight: 700; "
            "background: transparent;"
        )
        readiness_card.layout.addWidget(self._readiness_count)

        self._readiness_checks["site"].stateChanged.connect(
            self._confirm_site_marking
        )

        self._readiness_checks["labs"].stateChanged.connect(
            self._confirm_lab_results
        )

        layout.addWidget(readiness_card)

        # --- Surgical Plan card ---
        plan_card = SidebarCard("Surgical Plan")

        plan_card.add_row("Approach", "Laparoscopic", bold=True)
        plan_card.add_row("Target", "Gallbladder", bold=True)
        plan_card.add_row("Position", "Supine")

        plan_card.layout.addWidget(hline())

        consideration_title = QLabel("KEY CONSIDERATION")
        consideration_title.setObjectName("SidebarCardTitle")
        plan_card.layout.addWidget(consideration_title)

        consideration = QLabel(
            "Inflammation around the cystic duct. "
            "Review imaging before proceeding."
        )
        consideration.setWordWrap(True)
        consideration.setStyleSheet(
            "color: #CBD5E1; font-size: 10px; "
            "font-weight: 600; line-height: 1.3; "
            "background: transparent;"
        )
        plan_card.layout.addWidget(consideration)

        plan_card.layout.addStretch(1)

        # Let the Surgical Plan absorb the remaining sidebar height.
        plan_card.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(plan_card, 1)

        layout.addStretch(1)

        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)
    def set_system_status(self, component, state):
        """Compatibility hook for the global status updates.

        System Status is no longer shown as a separate card because the
        sidebar layout now uses that space for patient vitals.
        """
        return
    def set_system_statuses(self, statuses: dict):
        """Update multiple colour-only system status indicators."""
        if not isinstance(statuses, dict):
            return

        for component, state in statuses.items():
            self.set_system_status(component, str(state))


    def set_vitals_model(self, model):
        """Connect to the Authoritative PatientVitalsModel."""
        self._vitals_model = model
        model.vitals_updated.connect(self.update_vitals)
        self.update_vitals(model.get_display_data())

    def update_vitals(self, data: dict):
        """Update vitals displays from authoritative model data dictionary."""
        if not isinstance(data, dict):
            return

        hr_str = data.get("hr", "--")
        spo2_str = data.get("spo2", "--")
        bp_str = data.get("bp", "--")
        temp_str = data.get("temperature", "--")
        status = data.get("status", "NO DATA")
        is_live = data.get("is_live", False)
        is_stale = data.get("is_stale", False)

        if "HR" in self._vital_val_labels:
            self._vital_val_labels["HR"].setText(str(hr_str))
        if "SpO2" in self._vital_val_labels:
            self._vital_val_labels["SpO2"].setText(str(spo2_str))
        if "BP" in self._vital_val_labels:
            self._vital_val_labels["BP"].setText(str(bp_str))
        if "Temp" in self._vital_val_labels:
            self._vital_val_labels["Temp"].setText(str(temp_str))

        self.set_vitals_status(status, is_live, is_stale)

    def set_vitals_status(self, status: str, is_live: bool = False, is_stale: bool = False):
        """Update vitals status badge and color theme."""
        status_styles = {
            "LIVE": ("color: #10B981; background: rgba(16, 185, 129, 0.15);", "#10B981", "#38BDF8", "#F5F7FA", "#F5F7FA"),
            "STALE": ("color: #F59E0B; background: rgba(245, 158, 11, 0.15);", "#F59E0B", "#F59E0B", "#F59E0B", "#F59E0B"),
            "DISCONNECTED": ("color: #EF4444; background: rgba(239, 68, 68, 0.15);", "#94A3B8", "#94A3B8", "#94A3B8", "#94A3B8"),
            "NO DATA": ("color: #94A3B8; background: rgba(148, 163, 184, 0.15);", "#94A3B8", "#94A3B8", "#94A3B8", "#94A3B8"),
            "INVALID": ("color: #EF4444; background: rgba(239, 68, 68, 0.15);", "#94A3B8", "#94A3B8", "#94A3B8", "#94A3B8"),
        }
        badge_style, c_hr, c_spo2, c_bp, c_temp = status_styles.get(
            status, ("color: #94A3B8; background: rgba(148, 163, 184, 0.15);", "#94A3B8", "#94A3B8", "#94A3B8", "#94A3B8")
        )

        self._vitals_status_label.setText(status)
        self._vitals_status_label.setStyleSheet(
            f"font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; {badge_style}"
        )

        if "HR" in self._vital_val_labels:
            self._vital_val_labels["HR"].setStyleSheet(f"font-size: 26px; color: {c_hr};")
        if "SpO2" in self._vital_val_labels:
            self._vital_val_labels["SpO2"].setStyleSheet(f"font-size: 26px; color: {c_spo2};")
        if "BP" in self._vital_val_labels:
            self._vital_val_labels["BP"].setStyleSheet(f"font-size: 26px; color: {c_bp};")
        if "Temp" in self._vital_val_labels:
            self._vital_val_labels["Temp"].setStyleSheet(f"font-size: 26px; color: {c_temp};")

