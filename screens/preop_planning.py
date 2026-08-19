"""
Pre-Operation Planning — Stage 1 Redesign.

Layout (3-column, ultrawide-optimised):
  Left  (~25%): Information panel — Patient, Procedure, Imaging Status, Planning
  Center(~52%): MRI/CT imaging workspace — selector, viewer, controls
  Right (~23%): Surgeon Notes + Dermoscopic image

Medical images:
  Default MRI sample: assets/medical/mri_sample.png
  Default CT  sample: assets/medical/ct_sample.png
  User can load any PNG/JPG/JPEG/TIFF via Load Image button.
  DICOM filter preserved for future integration.
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QPushButton, QFileDialog, QTextEdit,
    QScrollArea, QGridLayout,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont

from widgets.card import PanelFrame
from theme_manager import ThemeManager

# Base directory for asset resolution
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MRI_SAMPLE = os.path.join(_BASE_DIR, "assets", "medical", "mri_sample.png")
_CT_SAMPLE  = os.path.join(_BASE_DIR, "assets", "medical", "ct_sample.png")


# ═══════════════════════════════════════════════════════════════════
#  MEDICAL IMAGE VIEWER
# ═══════════════════════════════════════════════════════════════════

class MedicalImageViewer(QFrame):
    """
    Displays a medical scan image (MRI or CT) within a professional black frame.
    Supports: load from file (PNG/JPG/TIFF/DICOM), zoom in/out, fit-to-view.
    Default: shows the supplied sample image for the selected modality.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MedicalViewer")
        self.setMinimumHeight(380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._pixmap: QPixmap | None = None
        self._zoom = 1.0
        self._image_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Actual image display label
        self._image_label = QLabel()
        self._image_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._image_label.setMinimumHeight(360)
        self._image_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._image_label.setStyleSheet("background-color: #000000; border-radius: 0px;")
        layout.addWidget(self._image_label, 1)

        tm = ThemeManager.instance()
        tm.theme_changed.connect(lambda _: self._refresh_display())

    def load_sample(self, path: str):
        """Load a default sample image."""
        if os.path.exists(path):
            self._pixmap = QPixmap(path)
            self._zoom = 1.0
            self._image_path = path
            self._refresh_display()
        else:
            self._pixmap = None
            self._image_path = ""
            self._show_placeholder()

    def load_file(self, path: str):
        """Load a user-selected image file."""
        pm = QPixmap(path)
        if not pm.isNull():
            self._pixmap = pm
            self._zoom = 1.0
            self._image_path = path
            self._refresh_display()
            return True
        return False

    def zoom_in(self):
        self._zoom = min(4.0, self._zoom + 0.25)
        self._refresh_display()

    def zoom_out(self):
        self._zoom = max(0.25, self._zoom - 0.25)
        self._refresh_display()

    def fit_to_view(self):
        self._zoom = 1.0
        self._refresh_display()

    def get_filename(self) -> str:
        return os.path.basename(self._image_path) if self._image_path else ""

    def has_image(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    def _refresh_display(self):
        if self._pixmap and not self._pixmap.isNull():
            # Scale keeping aspect ratio, adjusted by zoom
            available = self._image_label.size()
            if available.width() < 10 or available.height() < 10:
                # Use a reasonable default if not yet shown
                available = QSize(600, 400)
            target_w = int(available.width() * self._zoom)
            target_h = int(available.height() * self._zoom)
            scaled = self._pixmap.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)
        else:
            self._show_placeholder()

    def _show_placeholder(self):
        self._image_label.setPixmap(QPixmap())
        self._image_label.setText("NO IMAGE LOADED")
        self._image_label.setStyleSheet(
            "background-color: #000000; color: #2D3748; "
            "font-size: 13px; font-weight: 600; letter-spacing: 1px;"
            "border-radius: 0px;"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_display()


# ═══════════════════════════════════════════════════════════════════
#  DERMOSCOPIC IMAGE VIEWER (compact)
# ═══════════════════════════════════════════════════════════════════

class DermoscopicViewer(QFrame):
    """Small viewer for dermoscopic images with a Load button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DermoscopicViewer")
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(180)

        self._pixmap = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._image_label.setStyleSheet(
            "background-color: #0D1117; color: #2D3748; "
            "font-size: 11px; font-weight: 500;"
        )
        self._image_label.setText("DERMOSCOPIC  ·  NOT LOADED")
        layout.addWidget(self._image_label, 1)

    def load_file(self, path: str):
        pm = QPixmap(path)
        if not pm.isNull():
            self._pixmap = pm
            available_w = max(self.width() - 4, 200)
            available_h = max(self.height() - 4, 140)
            scaled = pm.scaled(
                available_w, available_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)
            self._image_label.setText("")
            return True
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap and not self._pixmap.isNull():
            available_w = max(self.width() - 4, 200)
            available_h = max(self.height() - 4, 140)
            scaled = self._pixmap.scaled(
                available_w, available_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)


# ═══════════════════════════════════════════════════════════════════
#  INFO SECTION WIDGET (compact label/value rows)
# ═══════════════════════════════════════════════════════════════════

class _InfoSection(QFrame):
    """Compact information section with a title and label/value rows."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoSectionCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)

        t = QLabel(title.upper())
        t.setObjectName("InfoSectionTitle")
        lay.addWidget(t)
        self._body = lay

    def add_row(self, label: str, value: str, value_style: str = ""):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lbl = QLabel(label)
        lbl.setObjectName("FieldLabel")
        lbl.setFixedWidth(96)
        val = QLabel(value)
        val.setObjectName("FieldValueBold")
        val.setWordWrap(True)
        if value_style:
            val.setStyleSheet(value_style)
        row.addWidget(lbl)
        row.addWidget(val, 1)
        self._body.addLayout(row)

    def add_widget(self, w):
        self._body.addWidget(w)


# ═══════════════════════════════════════════════════════════════════
#  MAIN PRE-OP PLANNING SCREEN
# ═══════════════════════════════════════════════════════════════════

class PreopPlanningScreen(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        # Track state
        self._active_modality = "MRI"   # "MRI" or "CT"
        self._mri_viewer: MedicalImageViewer | None = None
        self._ct_viewer:  MedicalImageViewer | None = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(14)

        # ──────────────────────────────────────────────────────────
        #  LEFT COLUMN (~25%) — Information panel
        # ──────────────────────────────────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMaximumWidth(320)
        left_scroll.setMinimumWidth(240)

        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 4, 0)
        left.setSpacing(10)

        # ── Patient section ──
        patient_sec = _InfoSection("Patient")
        patient_sec.add_row("Name", "Marisa Köhler")
        patient_sec.add_row("MRN", "0048-23119")
        patient_sec.add_row("Age / Sex", "58  ·  F")
        patient_sec.add_row("Blood", "O+")
        patient_sec.add_row("Allergies", "Penicillin",
                            "color: #F59E0B; font-weight: 700; font-size: 13px;")
        patient_sec.add_row("Diagnosis", "Acute Cholecystitis")
        left.addWidget(patient_sec)

        # ── Procedure section ──
        proc_sec = _InfoSection("Procedure")
        proc_sec.add_row("Type", "Laparoscopic Cholecystectomy")
        proc_sec.add_row("Code", "ICD-10 K80.20")
        proc_sec.add_row("Surgeon", "Dr. A. Voss")
        proc_sec.add_row("Assist", "Dr. R. Iyer")
        proc_sec.add_row("OR Suite", "Suite 03")
        proc_sec.add_row("Start", "08:42 UTC")
        proc_sec.add_row("Phase", "Dissection  ·  4 / 7")
        left.addWidget(proc_sec)

        # ── Imaging section ──
        img_sec = _InfoSection("Imaging")
        img_sec.add_row("Modality", "MRI / CT Dual")

        self._img_status_mri = QLabel("MRI: Sample loaded")
        self._img_status_mri.setObjectName("ImageStatusLabel")
        self._img_status_mri.setWordWrap(True)
        img_sec.add_widget(self._img_status_mri)

        self._img_status_ct = QLabel("CT: Sample loaded")
        self._img_status_ct.setObjectName("ImageStatusLabel")
        self._img_status_ct.setWordWrap(True)
        img_sec.add_widget(self._img_status_ct)

        self._img_filename = QLabel("")
        self._img_filename.setObjectName("ImageStatusLabelIdle")
        self._img_filename.setWordWrap(True)
        img_sec.add_widget(self._img_filename)
        left.addWidget(img_sec)

        # ── Planning section ──
        plan_sec = _InfoSection("Planning")
        plan_sec.add_row("Reg. Mode", "Rigid + B-Spline")
        plan_sec.add_row("RMS Error", "0.42 mm",
                         "color: #10B981; font-weight: 700; font-size: 13px;")
        plan_sec.add_row("Seg. Model", "nnU-Net v2.3")
        plan_sec.add_row("Dice Score", "0.937",
                         "color: #10B981; font-weight: 700; font-size: 13px;")
        plan_sec.add_row("ROI Defined", "3 regions")
        plan_sec.add_row("Plan Status", "APPROVED",
                         "color: #10B981; font-weight: 700; font-size: 13px;")
        left.addWidget(plan_sec)

        left.addStretch()
        left_scroll.setWidget(left_widget)
        outer.addWidget(left_scroll, 25)

        # ──────────────────────────────────────────────────────────
        #  CENTER COLUMN (~52%) — Imaging workspace
        # ──────────────────────────────────────────────────────────
        center = QVBoxLayout()
        center.setSpacing(8)
        center.setContentsMargins(0, 0, 0, 0)

        # ── Modality selector row ──
        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(0, 0, 0, 0)
        selector_row.setSpacing(8)

        selector_container = QFrame()
        selector_container.setObjectName("ScanSelectorContainer")
        selector_container.setFixedHeight(48)
        sc_lay = QHBoxLayout(selector_container)
        sc_lay.setContentsMargins(4, 4, 4, 4)
        sc_lay.setSpacing(4)

        self._btn_mri = QPushButton("MRI SCAN")
        self._btn_mri.setProperty("class", "ScanTab")
        self._btn_mri.setProperty("active", "true")
        self._btn_mri.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mri.clicked.connect(lambda: self._select_modality("MRI"))

        self._btn_ct = QPushButton("CT SCAN")
        self._btn_ct.setProperty("class", "ScanTab")
        self._btn_ct.setProperty("active", "false")
        self._btn_ct.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_ct.clicked.connect(lambda: self._select_modality("CT"))

        sc_lay.addWidget(self._btn_mri)
        sc_lay.addWidget(self._btn_ct)

        selector_row.addWidget(selector_container)
        selector_row.addStretch()

        # Load image button
        self._btn_load = QPushButton("LOAD IMAGE")
        self._btn_load.setProperty("class", "LoadImageButton")
        self._btn_load.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_load.clicked.connect(self._load_image)
        selector_row.addWidget(self._btn_load)
        center.addLayout(selector_row)

        # ── Viewer header ──
        viewer_header = QHBoxLayout()
        viewer_header.setContentsMargins(2, 0, 2, 0)
        self._modality_title = QLabel("MRI SCAN  ·  SAMPLE")
        self._modality_title.setObjectName("ViewerModalityTitle")
        self._viewer_meta = QLabel("BRAIN  ·  CORONAL  ·  T2W")
        self._viewer_meta.setObjectName("ViewerMetaLabel")
        viewer_header.addWidget(self._modality_title)
        viewer_header.addStretch()
        viewer_header.addWidget(self._viewer_meta)
        center.addLayout(viewer_header)

        # ── Image viewer stack ──
        # MRI viewer
        self._mri_viewer = MedicalImageViewer()
        self._mri_viewer.load_sample(_MRI_SAMPLE)
        center.addWidget(self._mri_viewer, 1)

        # CT viewer (hidden initially)
        self._ct_viewer = MedicalImageViewer()
        self._ct_viewer.load_sample(_CT_SAMPLE)
        self._ct_viewer.setVisible(False)
        center.addWidget(self._ct_viewer, 1)

        # ── Viewer controls ──
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setSpacing(8)

        btn_zoom_in = QPushButton("Zoom In  +")
        btn_zoom_in.setProperty("class", "ViewerControl")
        btn_zoom_in.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_zoom_in.clicked.connect(self._zoom_in)

        btn_zoom_out = QPushButton("Zoom Out  −")
        btn_zoom_out.setProperty("class", "ViewerControl")
        btn_zoom_out.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_zoom_out.clicked.connect(self._zoom_out)

        btn_fit = QPushButton("Fit to View")
        btn_fit.setProperty("class", "ViewerControl")
        btn_fit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_fit.clicked.connect(self._fit_view)

        ctrl_row.addWidget(btn_zoom_in)
        ctrl_row.addWidget(btn_zoom_out)
        ctrl_row.addWidget(btn_fit)
        ctrl_row.addStretch()

        self._zoom_label = QLabel("1.0×")
        self._zoom_label.setObjectName("ViewerMetaLabel")
        ctrl_row.addWidget(self._zoom_label)
        center.addLayout(ctrl_row)

        center_widget = QWidget()
        center_widget.setLayout(center)
        outer.addWidget(center_widget, 52)

        # ──────────────────────────────────────────────────────────
        #  RIGHT COLUMN (~23%) — Surgeon Notes + Dermoscopic
        # ──────────────────────────────────────────────────────────
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setMaximumWidth(360)
        right_scroll.setMinimumWidth(220)

        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(4, 0, 0, 0)
        right.setSpacing(10)

        # ── Surgeon Notes ──
        notes_section = _InfoSection("Surgeon Notes")
        self.surgery_notes = QTextEdit()
        self.surgery_notes.setObjectName("SurgeryNotes")
        self.surgery_notes.setPlaceholderText(
            "Enter intraoperative notes here…"
        )
        self.surgery_notes.setPlainText(
            "Patient positioned supine.\n"
            "Port placement complete.\n"
            "Cystic duct and artery identified.\n"
            "Dissection in progress — Critical View of Safety achieved.\n"
            "Blood loss minimal."
        )
        self.surgery_notes.setMinimumHeight(200)
        notes_section.add_widget(self.surgery_notes)
        right.addWidget(notes_section)

        # ── Dermoscopic image ──
        derm_section = _InfoSection("Dermoscopic Imaging")

        self._derm_viewer = DermoscopicViewer()
        derm_section.add_widget(self._derm_viewer)

        btn_load_derm = QPushButton("LOAD DERMOSCOPIC IMAGE")
        btn_load_derm.setProperty("class", "LoadImageButton")
        btn_load_derm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_load_derm.clicked.connect(self._load_dermoscopic)
        derm_section.add_widget(btn_load_derm)

        self._derm_status = QLabel("No image loaded")
        self._derm_status.setObjectName("ImageStatusLabelIdle")
        derm_section.add_widget(self._derm_status)
        right.addWidget(derm_section)

        right.addStretch()
        right_scroll.setWidget(right_widget)
        outer.addWidget(right_scroll, 23)

    # ── Modality selector ────────────────────────────────────────

    def _select_modality(self, modality: str):
        self._active_modality = modality

        # Update tab button states
        self._btn_mri.setProperty("active", "true" if modality == "MRI" else "false")
        self._btn_ct.setProperty("active",  "true" if modality == "CT"  else "false")
        for btn in (self._btn_mri, self._btn_ct):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # Show/hide viewers
        self._mri_viewer.setVisible(modality == "MRI")
        self._ct_viewer.setVisible(modality == "CT")

        # Update header labels
        active_viewer = self._mri_viewer if modality == "MRI" else self._ct_viewer
        if modality == "MRI":
            self._modality_title.setText("MRI SCAN  ·  BRAIN")
            self._viewer_meta.setText("CORONAL  ·  T2W")
        else:
            self._modality_title.setText("CT SCAN  ·  BRAIN")
            self._viewer_meta.setText("AXIAL  ·  NON-CONTRAST")

        # Update filename display
        fname = active_viewer.get_filename()
        if fname and "sample" not in fname.lower():
            self._img_filename.setText(f"Loaded: {fname}")
        else:
            self._img_filename.setText("")

        # Update zoom label
        zoom = active_viewer._zoom
        self._zoom_label.setText(f"{zoom:.1f}×")

    # ── Load image ───────────────────────────────────────────────

    def _load_image(self):
        """Load a new image for the currently active modality."""
        modality = self._active_modality
        path, _ = QFileDialog.getOpenFileName(
            self, f"Load {modality} Image", "",
            "Image Files (*.png *.jpg *.jpeg *.tiff *.tif);;"
            "Medical Images (*.dcm *.nii *.nii.gz *.mha *.nrrd);;"
            "All Files (*)"
        )
        if not path:
            return

        viewer = self._mri_viewer if modality == "MRI" else self._ct_viewer
        ok = viewer.load_file(path)

        if ok:
            fname = os.path.basename(path)
            if modality == "MRI":
                self._img_status_mri.setText(f"MRI: {fname}")
                self._img_status_mri.setObjectName("ImageStatusLabel")
            else:
                self._img_status_ct.setText(f"CT: {fname}")
                self._img_status_ct.setObjectName("ImageStatusLabel")
            self._img_filename.setText(f"Loaded: {fname}")
            self._modality_title.setText(
                f"{'MRI' if modality == 'MRI' else 'CT'} SCAN  ·  {fname[:24]}"
            )
            self._zoom_label.setText("1.0×")

    # ── Dermoscopic ──────────────────────────────────────────────

    def _load_dermoscopic(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dermoscopic Image", "",
            "Image Files (*.png *.jpg *.jpeg *.tiff *.tif);;All Files (*)"
        )
        if path:
            ok = self._derm_viewer.load_file(path)
            if ok:
                fname = os.path.basename(path)
                self._derm_status.setText(f"Loaded: {fname}")
                self._derm_status.setObjectName("ImageStatusLabel")

    # ── Viewer controls ──────────────────────────────────────────

    def _active_viewer(self) -> MedicalImageViewer:
        return self._mri_viewer if self._active_modality == "MRI" else self._ct_viewer

    def _zoom_in(self):
        v = self._active_viewer()
        v.zoom_in()
        self._zoom_label.setText(f"{v._zoom:.1f}×")

    def _zoom_out(self):
        v = self._active_viewer()
        v.zoom_out()
        self._zoom_label.setText(f"{v._zoom:.1f}×")

    def _fit_view(self):
        v = self._active_viewer()
        v.fit_to_view()
        self._zoom_label.setText("1.0×")
