from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from PyQt6.QtCore import (
    QSize,
    QMargins,
    Qt,
    QPointF,
    QRectF,
    QRunnable,
    QThreadPool,
    QObject,
    pyqtSignal,
    QTimer,
)
from PyQt6.QtGui import (
    QColor,
    QBrush,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QImage,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QInputDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QScrollArea,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import numpy as np

try:
    import pydicom
    from pydicom.pixels import apply_modality_lut, apply_voi_lut
except ImportError:
    pydicom = None
    apply_modality_lut = apply_voi_lut = None


def dicom_frame_pixmaps(path: str) -> tuple[list[QPixmap], str | None]:
    """Decode DICOM frames without changing source data; return user-safe error."""
    if pydicom is None:
        return [], "DICOM support is unavailable. Install pydicom in the application environment."
    try:
        dataset = pydicom.dcmread(path)
        if "PixelData" not in dataset:
            return [], "This DICOM file does not contain image pixel data."
        pixels = np.asarray(dataset.pixel_array)
        if int(getattr(dataset, "SamplesPerPixel", 1)) != 1:
            return [], "Color or multi-sample DICOM images are not supported yet."
        if pixels.ndim == 2:
            pixels = pixels[np.newaxis, ...]
        elif pixels.ndim > 3:
            return [], "This DICOM image uses an unsupported pixel layout."
        frames = []
        for frame in pixels:
            display = frame
            try:
                display = apply_modality_lut(display, dataset)
                display = apply_voi_lut(display, dataset)
            except (AttributeError, ValueError, TypeError):
                pass
            display = np.asarray(display, dtype=np.float64)
            finite = np.isfinite(display)
            if not finite.any():
                return [], "This DICOM image contains no displayable pixel values."
            low, high = np.percentile(display[finite], (0.5, 99.5))
            if high <= low:
                low, high = float(display[finite].min()), float(display[finite].max())
            if high <= low:
                high = low + 1
            gray = np.clip((display - low) * (255.0 / (high - low)), 0, 255).astype(np.uint8)
            gray[~finite] = 0
            if str(getattr(dataset, "PhotometricInterpretation", "MONOCHROME2")) == "MONOCHROME1":
                gray = 255 - gray
            h, w = gray.shape
            image = QImage(gray.data, w, h, int(gray.strides[0]), QImage.Format.Format_Grayscale8).copy()
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                return [], "The DICOM pixels could not be converted for display."
            frames.append(pixmap)
        if not frames:
            return [], "This DICOM file contains no displayable frames."
        return frames, None
    except Exception:
        # Decoder backends may raise implementation-specific errors. Do not
        # expose them or log any DICOM metadata in the UI.
        return [], "The DICOM file could not be decoded. It may be corrupt or use an unsupported transfer syntax."


class ReportSummarizer:
    """Configurable OpenAI-compatible remote backend; no credentials in source."""

    @staticmethod
    def configuration_error() -> str | None:
        provider = os.getenv("AI_PROVIDER", "").strip().lower()
        required = ("AI_API_KEY", "AI_MODEL", "AI_API_URL")
        if not provider or any(not os.getenv(key, "").strip() for key in required):
            return "AI summarization is not configured."
        if provider not in ("openai_compatible", "openai-compatible"):
            return "Configured AI provider is unsupported. Use an OpenAI-compatible endpoint."
        return None

    def summarize(self, text: str) -> str:
        configuration_error = self.configuration_error()
        if configuration_error:
            raise RuntimeError(configuration_error)
        provider = os.getenv("AI_PROVIDER", "").strip().lower()
        key = os.getenv("AI_API_KEY", "").strip()
        model = os.getenv("AI_MODEL", "").strip()
        endpoint = os.getenv("AI_API_URL", "").strip()
        if not text.strip():
            raise ValueError("No report text is available to summarize.")
        payload = json.dumps({"model": model, "messages": [
        {"role": "system", "content": "Summarize only facts explicitly stated in this report. Do not infer, diagnose, or invent. Return exactly two clearly labeled sections: Summary and Key Findings."},
            {"role": "user", "content": text[:100000]},
        ]}).encode("utf-8")
        request = urllib.request.Request(endpoint, data=payload, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read(1_000_000).decode("utf-8"))
            result = data["choices"][0]["message"]["content"]
            if not isinstance(result, str) or not result.strip():
                raise ValueError("AI service returned an empty summary.")
            return result.strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Unable to generate a report summary. Check the AI service configuration and connection.") from exc


class ReportTaskSignals(QObject):
    text_extracted = pyqtSignal(str, str)
    summary_ready = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

class PdfTextExtractionTask(QRunnable):
    """Extract report text off the GUI thread, without touching widgets."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = ReportTaskSignals()

    def run(self):
        try:
            self.signals.text_extracted.emit(self.path, extract_pdf_text(self.path))
        except Exception as exc:
            message = str(exc) if isinstance(exc, (OSError, ValueError, RuntimeError)) else "Unable to extract report text or generate a summary."
            self.signals.failed.emit(self.path, message)


class ReportSummaryTask(QRunnable):
    """Make the optional AI request off the GUI thread."""

    def __init__(self, path: str, text: str):
        super().__init__()
        self.path = path
        self.text = text
        self.signals = ReportTaskSignals()

    def run(self):
        try:
            summary = ReportSummarizer().summarize(self.text)
            self.signals.summary_ready.emit(self.path, summary)
        except Exception as exc:
            message = str(exc) if isinstance(exc, (OSError, ValueError, RuntimeError)) else "Unable to generate a report summary."
            self.signals.failed.emit(self.path, message)

try:
    from PyQt6.QtPdf import QPdfDocument
    from PyQt6.QtPdfWidgets import QPdfView

    PDF_AVAILABLE = True
except ImportError:
    QPdfDocument = None
    QPdfView = None
    PDF_AVAILABLE = False

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def extract_pdf_text(path: str) -> str:
    """Extract actual embedded PDF text with conservative size/page limits."""
    if PdfReader is None:
        raise RuntimeError("PDF text extraction is unavailable. Install pypdf.")
    if os.path.getsize(path) > 25 * 1024 * 1024:
        raise ValueError("This PDF is larger than the 25 MB text-extraction limit.")
    reader = PdfReader(path, strict=False)
    if len(reader.pages) > 250:
        raise ValueError("This PDF has more than 250 pages and cannot be summarized here.")
    parts = []
    total_chars = 0
    for page in reader.pages:
        extracted = page.extract_text() or ""
        if extracted.strip():
            remaining = 200000 - total_chars
            if remaining <= 0:
                break
            part = extracted[:remaining]
            parts.append(part)
            total_chars += len(part)
    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError("No selectable text was found. This may be a scanned or image-only PDF; OCR is not enabled.")
    return text[:200000]


# ---------------------------------------------------------------------------
# AETHER / Surgeon Console local palette.
# Explicit values are used here so this screen does not inherit fallback
# colours from an unknown ThemeManager key and turn text magenta/purple.
# ---------------------------------------------------------------------------

BG = "#0D1117"
SURFACE = "#0F1419"
PANEL = "#131820"
CARD = "#171C22"
INPUT = "#161B22"
BORDER = "#1C2333"
BORDER_HEAVY = "#2D3748"

TEXT = "#F5F7FA"
TEXT_SECONDARY = "#E6EDF3"
TEXT_MUTED = "#6B7B8D"
TEXT_DIM = "#4A5568"

BLUE = "#0095FF"
GREEN = "#10B981"
AMBER = "#F59E0B"
RED = "#EF4444"


def make_button(
    text: str,
    *,
    primary: bool = False,
    danger: bool = False,
    compact: bool = False,
) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(34 if compact else 38)

    if compact:
        button.setMinimumWidth(72)
    else:
        button.setMinimumWidth(104)

    if danger:
        normal = RED
        hover = "#F87171"
    elif primary:
        normal = BLUE
        hover = "#2AA8FF"
    else:
        normal = INPUT
        hover = "#202938"

    button.setStyleSheet(
        f"""
        QPushButton {{
            background: {normal};
            color: {TEXT};
            border: 1px solid {BORDER_HEAVY};
            border-radius: 6px;
            padding: 0 12px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.4px;
        }}
        QPushButton:hover {{
            background: {hover};
            border-color: {BLUE};
        }}
        QPushButton:pressed {{
            background: {PANEL};
        }}
        """
    )
    return button


def make_tool_button(text: str, checkable: bool = False) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(34)
    button.setMinimumWidth(74)
    button.setStyleSheet(
        f"""
        QToolButton {{
            background: {INPUT};
            color: {TEXT_SECONDARY};
            border: 1px solid {BORDER};
            border-radius: 5px;
            padding: 0 10px;
            font-size: 11px;
            font-weight: 700;
        }}
        QToolButton:hover {{
            background: #202938;
            border-color: {BLUE};
            color: {TEXT};
        }}
        QToolButton:checked {{
            background: {BLUE};
            border-color: {BLUE};
            color: #FFFFFF;
        }}
        """
    )
    return button


def annotation_pen(kind: str) -> QPen:
    if kind == "marker":
        pen = QPen(QColor(245, 158, 11, 150), 18.0)
    elif kind == "roi":
        pen = QPen(QColor(GREEN), 3.0)
    else:
        pen = QPen(QColor(BLUE), 4.0)

    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class AnnotationCanvas(QGraphicsView):
    """Non-destructive image canvas.

    Annotation coordinates are stored in original-image pixel coordinates.
    QGraphicsView handles zooming and view-to-scene coordinate conversion.
    """

    annotations_changed = pyqtSignal(object)
    annotation_created = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self._pixmap = QPixmap()
        self._image_item: QGraphicsPixmapItem | None = None
        self.annotations: list[dict] = []

        self.tool = "select"
        self._drawing = False
        self._start = QPointF()
        self._current_stroke: dict | None = None
        self._current_item = None

        self._undo_stack: list[list[dict]] = []
        self._redo_stack: list[list[dict]] = []

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QBrush(QColor("#090D12")))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def set_content(self, pixmap: QPixmap, annotations: list[dict] | None = None):
        self._pixmap = pixmap
        self.annotations = copy.deepcopy(annotations or [])
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._rebuild_scene()
        self.fit_image()

    def _rebuild_scene(self):
        self.scene.clear()
        self._image_item = None

        if self._pixmap.isNull():
            self.scene.setSceneRect(QRectF(0, 0, 1000, 700))
            return

        self._image_item = self.scene.addPixmap(self._pixmap)
        self._image_item.setZValue(0)

        for index, annotation in enumerate(self.annotations):
            item = self._graphics_item_for_annotation(annotation)
            if item is not None:
                item.setData(0, index)
                item.setZValue(10)
                self.scene.addItem(item)

        self.scene.setSceneRect(
            QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
        )

    def _graphics_item_for_annotation(self, annotation: dict):
        kind = annotation.get("type")

        if kind in ("pen", "marker"):
            points = annotation.get("points", [])
            if len(points) < 2:
                return None

            path = QPainterPath(QPointF(float(points[0][0]), float(points[0][1])))
            for x, y in points[1:]:
                path.lineTo(float(x), float(y))

            item = QGraphicsPathItem(path)
            item.setPen(annotation_pen(kind))
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            return item

        if kind == "roi":
            values = annotation.get("rect", [0, 0, 0, 0])
            rect = QRectF(
                float(values[0]),
                float(values[1]),
                float(values[2]),
                float(values[3]),
            ).normalized()
            item = QGraphicsRectItem(rect)
            item.setPen(annotation_pen("roi"))
            item.setBrush(QBrush(QColor(16, 185, 129, 25)))
            return item

        return None

    def set_tool(self, tool: str):
        self.tool = tool
        self._drawing = False
        self._current_stroke = None
        self._current_item = None

        if tool in ("pen", "marker", "roi"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif tool == "eraser":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def fit_image(self):
        if self._image_item is not None:
            self.resetTransform()
            self.fitInView(
                self._image_item,
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def zoom_in(self):
        self.scale(1.20, 1.20)

    def zoom_out(self):
        self.scale(1 / 1.20, 1 / 1.20)

    def _push_undo(self):
        self._undo_stack.append(copy.deepcopy(self.annotations))
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return

        self._redo_stack.append(copy.deepcopy(self.annotations))
        self.annotations = self._undo_stack.pop()
        self._rebuild_scene()
        self.annotations_changed.emit(copy.deepcopy(self.annotations))

    def redo(self):
        if not self._redo_stack:
            return

        self._undo_stack.append(copy.deepcopy(self.annotations))
        self.annotations = self._redo_stack.pop()
        self._rebuild_scene()
        self.annotations_changed.emit(copy.deepcopy(self.annotations))

    def clear_annotations(self):
        if not self.annotations:
            return

        self._push_undo()
        self.annotations.clear()
        self._rebuild_scene()
        self.annotations_changed.emit(copy.deepcopy(self.annotations))

    def _scene_pos(self, event) -> QPointF:
        return self.mapToScene(event.position().toPoint())

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        pos = self._scene_pos(event)

        if self._pixmap.isNull():
            return

        image_rect = QRectF(
            0,
            0,
            self._pixmap.width(),
            self._pixmap.height(),
        )

        if not image_rect.contains(pos):
            return

        if self.tool in ("pen", "marker"):
            self._push_undo()
            self._drawing = True
            self._current_stroke = {
                "type": self.tool,
                "points": [[float(pos.x()), float(pos.y())]],
            }

            path = QPainterPath(pos)
            item = QGraphicsPathItem(path)
            item.setPen(annotation_pen(self.tool))
            item.setZValue(20)
            self.scene.addItem(item)
            self._current_item = item

        elif self.tool == "roi":
            self._push_undo()
            self._drawing = True
            self._start = pos
            item = QGraphicsRectItem(QRectF(pos, pos))
            item.setPen(annotation_pen("roi"))
            item.setBrush(QBrush(QColor(16, 185, 129, 25)))
            item.setZValue(20)
            self.scene.addItem(item)
            self._current_item = item

        elif self.tool == "eraser":
            items = self.scene.items(pos)
            for item in items:
                if item is self._image_item:
                    continue

                index = item.data(0)
                if isinstance(index, int) and 0 <= index < len(self.annotations):
                    self._push_undo()
                    self.annotations.pop(index)
                    self._rebuild_scene()
                    self.annotations_changed.emit(copy.deepcopy(self.annotations))
                    break

        event.accept()

    def mouseMoveEvent(self, event):
        if not self._drawing:
            return super().mouseMoveEvent(event)

        pos = self._scene_pos(event)

        if self.tool in ("pen", "marker"):
            if self._current_stroke is None or self._current_item is None:
                return

            points = self._current_stroke["points"]
            points.append([float(pos.x()), float(pos.y())])

            path = self._current_item.path()
            path.lineTo(pos)
            self._current_item.setPath(path)

        elif self.tool == "roi" and self._current_item is not None:
            rect = QRectF(self._start, pos).normalized()
            self._current_item.setRect(rect)

        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)

        if not self._drawing:
            return

        created_index = None
        if self.tool in ("pen", "marker"):
            if self._current_stroke is not None:
                points = self._current_stroke["points"]
                if len(points) >= 2:
                    self.annotations.append(copy.deepcopy(self._current_stroke))
                    created_index = len(self.annotations) - 1

        elif self.tool == "roi":
            if self._current_item is not None:
                rect = self._current_item.rect().normalized()
                if rect.width() >= 5 and rect.height() >= 5:
                    self.annotations.append(
                        {
                            "type": "roi",
                            "rect": [
                                float(rect.x()),
                                float(rect.y()),
                                float(rect.width()),
                                float(rect.height()),
                            ],
                        }
                    )
                    created_index = len(self.annotations) - 1

        self._drawing = False
        self._current_stroke = None
        self._current_item = None

        self._rebuild_scene()
        self.annotations_changed.emit(copy.deepcopy(self.annotations))
        if created_index is not None:
            self.annotation_created.emit(created_index)
        event.accept()


class AnnotationFullscreenViewer(QDialog):
    """True fullscreen image workspace with annotation controls below the image."""

    annotations_changed = pyqtSignal(object)

    def __init__(
        self,
        title: str,
        pixmap: QPixmap,
        annotations: list[dict],
        parent=None,
        frame_pixmaps: list[QPixmap] | None = None,
        frame_index: int = 0,
    ):
        super().__init__(parent)

        self.title = title
        self._closed = False
        self.frame_pixmaps = frame_pixmaps or [pixmap]
        self.frame_index = min(max(0, frame_index), len(self.frame_pixmaps) - 1)

        # Make this an independent, borderless top-level window.
        # Fullscreen is explicitly entered with showFullScreen() by the caller.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle(f"AETHER — {title}")
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The image is deliberately the dominant element.
        self.canvas = AnnotationCanvas(self)
        self.canvas.setStyleSheet(
            """
            QGraphicsView {
                background: #05080C;
                border: none;
            }
            """
        )
        self.canvas.set_content(self.frame_pixmaps[self.frame_index], annotations)
        root.addWidget(self.canvas, 1)

        # Everything the surgeon needs while marking the image lives below it.
        dock = QFrame()
        dock.setObjectName("AnnotationDock")
        dock.setFixedHeight(76)
        dock.setStyleSheet(
            f"""
            QFrame#AnnotationDock {{
                background: {PANEL};
                border-top: 1px solid {BORDER_HEAVY};
            }}
            """
        )

        dock_layout = QHBoxLayout(dock)
        dock_layout.setContentsMargins(18, 8, 18, 8)
        dock_layout.setSpacing(6)

        title_column = QVBoxLayout()
        title_column.setSpacing(2)

        title_label = QLabel(title.upper())
        title_label.setStyleSheet(
            f"""
            QLabel {{
                color: {TEXT};
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
            """
        )

        subtitle = QLabel("IMAGE REVIEW  •  ANNOTATIONS ARE SAVED ON RETURN")
        subtitle.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; letter-spacing: 0.6px;"
        )

        title_column.addWidget(title_label)
        title_column.addWidget(subtitle)
        dock_layout.addLayout(title_column)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet(f"color: {BORDER_HEAVY};")
        dock_layout.addWidget(divider)

        self.pen_btn = make_tool_button("PEN", True)
        self.marker_btn = make_tool_button("MARKER", True)
        self.roi_btn = make_tool_button("ROI", True)
        self.eraser_btn = make_tool_button("ERASER", True)

        for button, tool in (
            (self.pen_btn, "pen"),
            (self.marker_btn, "marker"),
            (self.roi_btn, "roi"),
            (self.eraser_btn, "eraser"),
        ):
            dock_layout.addWidget(button)
            button.clicked.connect(
                lambda checked=False, t=tool: self._select_tool(t)
            )

        divider2 = QFrame()
        divider2.setFrameShape(QFrame.Shape.VLine)
        divider2.setStyleSheet(f"color: {BORDER_HEAVY};")
        dock_layout.addWidget(divider2)

        undo = make_tool_button("UNDO")
        redo = make_tool_button("REDO")
        clear = make_tool_button("CLEAR")
        save = make_button("SAVE", primary=True, compact=True)

        dock_layout.addWidget(undo)
        dock_layout.addWidget(redo)
        dock_layout.addWidget(clear)
        dock_layout.addWidget(save)

        divider3 = QFrame()
        divider3.setFrameShape(QFrame.Shape.VLine)
        divider3.setStyleSheet(f"color: {BORDER_HEAVY};")
        dock_layout.addWidget(divider3)

        zoom_out = make_tool_button("−")
        fit = make_tool_button("FIT")
        zoom_in = make_tool_button("+")

        dock_layout.addWidget(zoom_out)
        dock_layout.addWidget(fit)
        dock_layout.addWidget(zoom_in)

        dock_layout.addStretch()

        self._count_label = QLabel("0 annotations")
        self._count_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10px; font-weight: 700;"
        )
        dock_layout.addWidget(self._count_label)

        notes_btn = make_tool_button("ANNOTATIONS / NOTES")
        notes_btn.setMinimumWidth(150)
        dock_layout.addWidget(notes_btn)
        notes_btn.clicked.connect(self._manage_notes)

        self._frame_label = QLabel()
        self._frame_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px; font-weight: 700;")
        prev_frame = make_tool_button("PREVIOUS")
        next_frame = make_tool_button("NEXT")
        prev_frame.clicked.connect(lambda: self._change_frame(-1))
        next_frame.clicked.connect(lambda: self._change_frame(1))
        if len(self.frame_pixmaps) > 1:
            dock_layout.addWidget(prev_frame)
            dock_layout.addWidget(self._frame_label)
            dock_layout.addWidget(next_frame)
        else:
            self._frame_label.hide()
        self._update_frame_label()

        close = make_button("RETURN TO GRID", primary=True, compact=True)
        close.setMinimumWidth(150)
        dock_layout.addWidget(close)

        root.addWidget(dock)

        undo.clicked.connect(self.canvas.undo)
        redo.clicked.connect(self.canvas.redo)
        clear.clicked.connect(self.canvas.clear_annotations)
        save.clicked.connect(self._save_now)
        fit.clicked.connect(self.canvas.fit_image)
        zoom_out.clicked.connect(self.canvas.zoom_out)
        zoom_in.clicked.connect(self.canvas.zoom_in)
        close.clicked.connect(self._return_to_grid)

        self.canvas.annotations_changed.connect(self._update_count)
        self.canvas.annotation_created.connect(self._prompt_annotation_note)

        self._select_tool("pen")
        self._update_count(self.canvas.annotations)

    def showEvent(self, event):
        super().showEvent(event)

        # The actual fullscreen geometry is available only after the window
        # has been shown. Fit afterwards so the image uses the entire viewport.
        QTimer.singleShot(0, self._enter_fullscreen_and_fit)
        QTimer.singleShot(100, self.canvas.fit_image)

    def _enter_fullscreen_and_fit(self):
        if not self.isFullScreen():
            self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.canvas.setFocus()
        self.canvas.fit_image()

    def _select_tool(self, tool: str):
        buttons = {
            "pen": self.pen_btn,
            "marker": self.marker_btn,
            "roi": self.roi_btn,
            "eraser": self.eraser_btn,
        }

        for key, button in buttons.items():
            button.setChecked(key == tool)

        self.canvas.set_tool(tool)

    def _update_count(self, annotations):
        count = len(annotations)
        self._count_label.setText(
            f"{count} annotation{'s' if count != 1 else ''}"
        )

    def _update_frame_label(self):
        self._frame_label.setText(f"SLICE {self.frame_index + 1} / {len(self.frame_pixmaps)}")

    def _change_frame(self, direction: int):
        if len(self.frame_pixmaps) <= 1:
            return
        self.frame_index = (self.frame_index + direction) % len(self.frame_pixmaps)
        self.canvas.set_content(self.frame_pixmaps[self.frame_index], self.canvas.annotations)
        self._update_frame_label()

    def _prompt_annotation_note(self, index: int):
        if not 0 <= index < len(self.canvas.annotations):
            return
        annotation = self.canvas.annotations[index]
        text, accepted = QInputDialog.getMultiLineText(
            self, "Add Annotation Note", "Annotation note (optional):", annotation.get("note", "")
        )
        if accepted:
            self.canvas._push_undo()
            annotation["note"] = text.strip()
            self.canvas._rebuild_scene()
            self.canvas.annotations_changed.emit(copy.deepcopy(self.canvas.annotations))

    def _manage_notes(self):
        if not self.canvas.annotations:
            QMessageBox.information(self, "Annotations", "There are no annotations to edit yet.")
            return
        labels = []
        for number, item in enumerate(self.canvas.annotations, 1):
            note = item.get("note", "").strip() or "No note"
            labels.append(f"{number:02d}  {item.get('type', 'annotation').upper()} — {note[:90]}")
        selected, accepted = QInputDialog.getItem(self, "Annotations", "Select annotation to edit its note:", labels, 0, False)
        if accepted:
            index = labels.index(selected)
            self._prompt_annotation_note(index)

    def _save_now(self):
        """Push the current annotations to the Pre-Op card immediately."""
        self.annotations_changed.emit(copy.deepcopy(self.canvas.annotations))

    def _save_and_return(self):
        # Always save immediately before leaving fullscreen.
        self._save_now()
        self._closed = True

        """Persist the current annotation model, then close the viewer."""
        if not self._closed:
            self._closed = True
            self.annotations_changed.emit(
                copy.deepcopy(self.canvas.annotations)
            )

        # Do NOT call showNormal() from closeEvent(). On Linux/X11, changing
        # the window state while Qt is already processing a close event can
        # leave a fullscreen dialog visible. close() itself exits fullscreen
        # as part of closing the window.
        self.close()

    def _return_to_grid(self):
        self._save_and_return()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._save_and_return()
            event.accept()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        # Save here too for window-manager close / Alt+F4. The explicit
        # Return-to-Grid and Escape paths save before calling close(), so this
        # guard prevents a duplicate update.
        if not self._closed:
            self._closed = True
            self.annotations_changed.emit(
                copy.deepcopy(self.canvas.annotations)
            )

        # Let Qt perform the fullscreen -> normal transition as part of the
        # close operation. This is more reliable on Linux/X11 than calling
        # showNormal() from inside closeEvent().
        event.accept()


class MedicalImageCard(QFrame):
    """Figma-style imaging card while preserving the existing image actions."""

    fullscreen_requested = pyqtSignal(str)
    annotate_requested = pyqtSignal(str)
    load_requested = pyqtSignal(str)

    _META = {
        "MRI": ("MRI Brain (Axial T2)", "Primary Lesion Overview", "09-24-2026 14:22"),
        "CT": ("CT Head (Contrast)", "Bony Architecture & Angio", "09-24-2026 15:10"),
        "X-RAY": ("X-Ray Chest (PA)", "Pre-Anesthesia Baseline", "09-25-2026 09:05"),
        "CLINICAL": ("PET Scan (FDG)", "Metabolic Activity Index", "09-25-2026 11:30"),
    }

    def __init__(self, study_name: str, image_path: str | None = None, parent=None):
        super().__init__(parent)

        self.study_name = study_name
        self.image_path = image_path
        self.pixmap = QPixmap()
        self.annotations: list[dict] = []
        self.frames: list[QPixmap] = []
        self.frame_index = 0

        title, subtitle_text, stamp = self._META.get(
            study_name,
            (study_name.upper(), "Imaging Study", "-- / -- / ---- --:--"),
        )
        self._title_text = title

        self.setObjectName("MedicalImageCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 800;"
        )
        subtitle = QLabel(subtitle_text)
        subtitle.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 8px; letter-spacing: 0.2px;"
        )
        title_col.addWidget(title_label)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        self.timestamp = QLabel(stamp)
        self.timestamp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.timestamp.setStyleSheet(
            f"color: {GREEN}; font-size: 8px; font-weight: 700;"
        )
        header.addWidget(self.timestamp)
        layout.addLayout(header)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(0, 0)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_label.setStyleSheet(
            f"QLabel {{ background: #090D12; border: 1px solid {BORDER}; border-radius: 5px; }}"
        )
        layout.addWidget(self.image_label, 1)

        # Compact action strip from the Figma treatment. Tooltips keep the
        # original three actions discoverable without changing behavior.
        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        action_row.addStretch()

        self.annotate_btn = self._make_icon_button("✎", "ANNOTATE")
        self.fullscreen_btn = self._make_icon_button("⛶", "FULL SCREEN")
        self.load_btn = self._make_icon_button("↥", "LOAD IMAGE")
        self.remove_btn = self._make_icon_button("×", "REMOVE IMAGE")

        action_row.addWidget(self.annotate_btn)
        action_row.addWidget(self.fullscreen_btn)
        action_row.addWidget(self.load_btn)
        action_row.addWidget(self.remove_btn)
        layout.addLayout(action_row)

        self.annotate_btn.clicked.connect(
            lambda: self.annotate_requested.emit(self.study_name)
        )
        self.fullscreen_btn.clicked.connect(
            lambda: self.fullscreen_requested.emit(self.study_name)
        )
        self.load_btn.clicked.connect(
            lambda: self.load_requested.emit(self.study_name)
        )
        self.remove_btn.clicked.connect(self.clear_image)

        self.setStyleSheet(
            f"""
            QFrame#MedicalImageCard {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 7px;
            }}
            QFrame#MedicalImageCard:hover {{
                border: 1px solid #314155;
            }}
            """
        )

        self.set_image(image_path)

    @staticmethod
    def _make_icon_button(symbol: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(symbol)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(28, 24)
        button.setStyleSheet(
            f"""
            QToolButton {{
                background: {INPUT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-radius: 5px;
                font-size: 13px;
                font-weight: 800;
            }}
            QToolButton:hover {{
                background: #202938;
                color: {TEXT};
                border-color: {BLUE};
            }}
            QToolButton:pressed {{
                background: {PANEL};
            }}
            """
        )
        return button

    def set_image(self, image_path: str | None):
        self.image_path = image_path
        self.frames = []
        self.frame_index = 0

        if image_path and os.path.exists(image_path):
            if Path(image_path).suffix.lower() in (".dcm", ".dicom"):
                self.frames, _error = dicom_frame_pixmaps(image_path)
                pixmap = self.frames[0] if self.frames else QPixmap()
            else:
                pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                self.pixmap = pixmap
                self.status_text("IMAGE LOADED", GREEN)
                self.remove_btn.setEnabled(True)
                self._update_preview()
                return

        self.pixmap = QPixmap()
        self.frames = []
        self.annotations = []
        self.remove_btn.setEnabled(False)
        self.status_text("NO IMAGE", TEXT_MUTED)
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("NO IMAGE")
        self.image_label.setStyleSheet(
            f"QLabel {{ background: #090D12; color: {TEXT_DIM}; border: 1px dashed {BORDER_HEAVY}; border-radius: 5px; font-size: 9px; font-weight: 800; }}"
        )

    def clear_image(self):
        """Remove the image from this card without deleting the source file."""
        self.image_path = None
        self.pixmap = QPixmap()
        self.frames = []
        self.frame_index = 0
        self.annotations = []

        self.remove_btn.setEnabled(False)
        self.status_text("NO IMAGE", TEXT_MUTED)

        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("NO IMAGE")
        self.image_label.setStyleSheet(
            f"QLabel {{ background: #090D12; color: {TEXT_DIM}; border: 1px dashed {BORDER_HEAVY}; border-radius: 5px; font-size: 9px; font-weight: 800; }}"
        )

    def set_dicom_frames(self, image_path: str, frames: list[QPixmap]):
        if not frames:
            return
        self.image_path = image_path
        self.frames = frames
        self.frame_index = 0
        self.pixmap = frames[0]
        self.remove_btn.setEnabled(True)
        self.status_text(f"DICOM • {len(frames)} FRAME{'S' if len(frames) != 1 else ''}", GREEN)
        self.image_label.setText("")
        self.image_label.setStyleSheet(
            f"QLabel {{ background: #090D12; border: 1px solid {BORDER}; border-radius: 5px; }}"
        )
        self._update_preview()

    def set_annotations(self, annotations: list[dict]):
        self.annotations = copy.deepcopy(annotations)
        if not self.pixmap.isNull():
            self._update_preview()

    def status_text(self, text: str, color: str):
        # The Figma card keeps the status compact and subordinate to the title.
        self.timestamp.setToolTip(text)
        self.timestamp.setStyleSheet(f"color: {color}; font-size: 8px; font-weight: 700;")

    def _update_preview(self):
        if self.pixmap.isNull():
            return

        target_size = self.image_label.size()
        if target_size.width() < 20 or target_size.height() < 20:
            return

        scaled = self.pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        canvas = QPixmap(target_size)
        canvas.fill(QColor("#090D12"))

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(
            (target_size.width() - scaled.width()) // 2,
            (target_size.height() - scaled.height()) // 2,
            scaled,
        )

        offset_x = (target_size.width() - scaled.width()) / 2.0
        offset_y = (target_size.height() - scaled.height()) / 2.0
        sx = scaled.width() / float(self.pixmap.width())
        sy = scaled.height() / float(self.pixmap.height())

        for annotation in self.annotations:
            kind = annotation.get("type")

            if kind in ("pen", "marker"):
                points = annotation.get("points", [])
                pen = annotation_pen(kind)
                pen.setWidthF(pen.widthF() * sx)
                painter.setPen(pen)
                for first, second in zip(points, points[1:]):
                    p1 = QPointF(
                        offset_x + float(first[0]) * sx,
                        offset_y + float(first[1]) * sy,
                    )
                    p2 = QPointF(
                        offset_x + float(second[0]) * sx,
                        offset_y + float(second[1]) * sy,
                    )
                    painter.drawLine(p1, p2)

            elif kind == "roi":
                values = annotation.get("rect", [0, 0, 0, 0])
                rect = QRectF(
                    offset_x + float(values[0]) * sx,
                    offset_y + float(values[1]) * sy,
                    float(values[2]) * sx,
                    float(values[3]) * sy,
                )
                painter.setPen(annotation_pen("roi"))
                painter.setBrush(QBrush(QColor(16, 185, 129, 25)))
                painter.drawRect(rect)

        painter.end()

        self.image_label.setText("")
        self.image_label.setStyleSheet(
            f"QLabel {{ background: #090D12; border: 1px solid {BORDER}; border-radius: 5px; }}"
        )
        self.image_label.setPixmap(canvas)

        if self.annotations:
            self.timestamp.setText(f"ANNOTATED • {len(self.annotations)}")
            self.timestamp.setStyleSheet(
                f"color: {AMBER}; font-size: 8px; font-weight: 700;"
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.pixmap.isNull():
            self._update_preview()


class ReportReviewDialog(QDialog):
    """Modal report workspace; keeps the main Pre-Op screen visually uncluttered."""

    def __init__(self, reports: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("AETHER — TEST REPORTS & DOCUMENTS")
        self.resize(1080, 760)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("TEST REPORTS & DOCUMENTS")
        title.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: 800; letter-spacing: 0.7px;")
        header.addWidget(title)
        header.addStretch()
        close_btn = make_button("CLOSE", compact=True)
        header.addWidget(close_btn)
        close_btn.clicked.connect(self.accept)
        root.addLayout(header)

        self.panel = EmbeddedReportsPanel(reports, self)
        self.panel.setMinimumWidth(0)
        self.panel.setMaximumWidth(16777215)
        self.panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.panel, 1)


class MiniSparkline(QWidget):
    """Tiny trend line used by the Figma-style vitals rail."""

    def __init__(self, values: list[float], color: str = GREEN, parent=None):
        super().__init__(parent)
        self.values = values
        self.color = color
        self.setMinimumSize(56, 22)
        self.setMaximumSize(76, 28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        del event
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self.color), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        minimum = min(self.values)
        maximum = max(self.values)
        span = (maximum - minimum) or 1.0
        margin_x = 2.0
        margin_y = 3.0
        width = max(1.0, self.width() - 2 * margin_x)
        height = max(1.0, self.height() - 2 * margin_y)
        points = []
        for i, value in enumerate(self.values):
            x = margin_x + width * (i / (len(self.values) - 1))
            y = margin_y + height * (1.0 - (value - minimum) / span)
            points.append(QPointF(x, y))
        for p1, p2 in zip(points, points[1:]):
            painter.drawLine(p1, p2)
        painter.end()


class PdfViewerDialog(QDialog):
    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"AETHER — REPORT — {Path(pdf_path).name}")
        self.resize(1200, 820)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        title = QLabel(Path(pdf_path).name)
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 13px; font-weight: 800;"
        )
        toolbar.addWidget(title)
        toolbar.addStretch()

        close_btn = make_button("CLOSE", compact=True)
        toolbar.addWidget(close_btn)
        close_btn.clicked.connect(self.accept)

        layout.addLayout(toolbar)

        if not os.path.isfile(pdf_path):
            message = QLabel("Unable to open this PDF. The selected file could not be found.")
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            message.setStyleSheet(f"color: {RED}; font-size: 13px; font-weight: 700;")
            layout.addWidget(message, 1)
            return

        if not PDF_AVAILABLE:
            message = QLabel(
                "Qt PDF is not available in this Python environment.\n\n"
                "Install it with:\n"
                "pip install PyQt6-Qt6\n\n"
                "Then restart the Surgeon Console."
            )
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            message.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 13px;"
            )
            layout.addWidget(message, 1)
            return

        self.document = QPdfDocument(self)
        self.pdf_view = QPdfView(self)
        self.pdf_view.setDocument(self.document)
        error = self.document.load(pdf_path)

        if error != QPdfDocument.Error.None_:
            message = QLabel(
                f"Unable to open this PDF.\n\n{error.name}"
            )
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            message.setStyleSheet(
                f"color: {RED}; font-size: 13px; font-weight: 700;"
            )
            layout.addWidget(message, 1)
            return

        # Multi-page mode gives the surgeon a continuous report-review view.
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)
        self.pdf_view.setStyleSheet(
            f"""
            QPdfView {{
                background: #090D12;
                border: 1px solid {BORDER};
            }}
            """
        )
        self.pdf_view.hide()
        layout.addWidget(self.pdf_view, 1)
        self._loading_label = QLabel("Loading PDF…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._loading_label)
        self.document.statusChanged.connect(self._document_status_changed)
        self._document_status_changed()

    def _document_status_changed(self, *_args):
        status = self.document.status()
        if status == QPdfDocument.Status.Ready:
            self.pdf_view.setVisible(True)
            self._loading_label.setVisible(False)
        elif status == QPdfDocument.Status.Error:
            self.pdf_view.setVisible(False)
            self._loading_label.setText("Unable to open this PDF.")


class PdfReportsDialog(QDialog):
    """Compact report manager so the main imaging grid stays uncluttered."""

    def __init__(self, reports: list[str], parent=None):
        super().__init__(parent)

        self.reports = reports
        self.setWindowTitle("AETHER — TEST REPORTS")
        self.resize(720, 480)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        heading = QLabel("TEST REPORTS")
        heading.setStyleSheet(
            f"color: {TEXT}; font-size: 15px; font-weight: 800; letter-spacing: 1px;"
        )
        root.addWidget(heading)

        sub = QLabel(
            "Upload and review PDF reports without taking space away from the imaging workspace."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
        )
        root.addWidget(sub)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            f"""
            QListWidget {{
                background: {CARD};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 5px;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 10px;
                border-bottom: 1px solid {BORDER};
            }}
            QListWidget::item:selected {{
                background: #172B3D;
                color: {TEXT};
            }}
            """
        )
        root.addWidget(self.list_widget, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(7)

        add = make_button("+ ADD PDF", primary=True)
        view = make_button("VIEW REPORT")
        close = make_button("CLOSE")

        buttons.addWidget(add)
        buttons.addWidget(view)
        buttons.addStretch()
        buttons.addWidget(close)

        root.addLayout(buttons)

        add.clicked.connect(self._add_pdf)
        view.clicked.connect(self._view_selected)
        close.clicked.connect(self.accept)
        self.list_widget.itemDoubleClicked.connect(
            lambda item: self._view_selected()
        )

        self._refresh()

    def _refresh(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        for path in self.reports:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setSizeHint(QSize(0, 38))
            self.list_widget.addItem(item)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 2, 4, 2)
            row_layout.setSpacing(6)

            name_label = QLabel(Path(path).name)
            name_label.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 12px;"
            )
            name_label.setToolTip(path)
            row_layout.addWidget(name_label, 1)

            remove_btn = QToolButton()
            remove_btn.setText("×")
            remove_btn.setToolTip("REMOVE PDF")
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.setFixedSize(28, 24)
            remove_btn.setStyleSheet(
                f"""
                QToolButton {{
                    background: {INPUT};
                    color: {TEXT_SECONDARY};
                    border: 1px solid {BORDER};
                    border-radius: 5px;
                    font-size: 16px;
                    font-weight: 800;
                }}
                QToolButton:hover {{
                    background: #202938;
                    color: {TEXT};
                    border-color: {BLUE};
                }}
                QToolButton:pressed {{
                    background: {PANEL};
                }}
                """
            )
            remove_btn.clicked.connect(
                lambda _checked=False, report_path=path:
                self._remove_pdf(report_path)
            )

            row_layout.addWidget(remove_btn)
            self.list_widget.setItemWidget(item, row)

        self.list_widget.blockSignals(False)

        count = len(self.reports)
        self.count_label.setText(
            f"{count} FILE" if count == 1 else f"{count} FILES"
        )

        if not self.reports:
            self._clear_selection()
        elif self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)

    def _remove_pdf(self, path: str):
        """Remove a PDF from the Pre-Op list without deleting the source file."""
        if path not in self.reports:
            return

        self.reports.remove(path)

        # If this PDF is currently selected, clear the viewer/selection.
        if self._selected_path == path or self._document_path == path:
            self._clear_selection()

            if self.pdf_view is not None:
                self.pdf_view.setVisible(False)

            if self._document is not None:
                self._document.close()

        # Keep the actual PDF file on disk.
        self.reports_changed.emit(self.reports)
        self._refresh()

    def _add_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Test Report",
            str(Path.home()),
            "PDF Files (*.pdf)",
        )

        if not path:
            return

        if path not in self.reports:
            self.reports.append(path)

        self._refresh()

    def _view_selected(self):
        item = self.list_widget.currentItem()

        if item is None:
            return

        path = item.data(Qt.ItemDataRole.UserRole)

        if not path or not os.path.isfile(path):
            QMessageBox.warning(
                self,
                "Report unavailable",
                "The selected PDF could not be found.",
            )
            return

        viewer = PdfViewerDialog(path, self)
        viewer.exec()


class EmbeddedReportsPanel(QFrame):
    """Embedded PDF test-report workspace for the Pre-Op screen."""

    reports_changed = pyqtSignal(object)

    def __init__(self, reports: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.reports = reports if reports is not None else []
        self._document = None
        self._document_path: str | None = None
        self._selected_path: str | None = None
        self._summaries: dict[str, str] = {}
        self._extracted_text: dict[str, str] = {}
        self._extraction_errors: dict[str, str] = {}
        self._text_tasks: dict[str, PdfTextExtractionTask] = {}
        self._summary_task: ReportSummaryTask | None = None
        self._summary_running = False
        self._worker_pool = QThreadPool.globalInstance()
        self.summarizer = ReportSummarizer()

        self.setObjectName("EmbeddedReportsPanel")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMinimumWidth(280)
        self.setMaximumWidth(480)
        self.setStyleSheet(
            f"""
            QFrame#EmbeddedReportsPanel {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel {{
                color: {TEXT};
            }}
            QListWidget {{
                background: {INPUT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px;
                font-size: 11px;
            }}
            QListWidget::item {{
                padding: 8px 7px;
                border-bottom: 1px solid {BORDER};
            }}
            QListWidget::item:selected {{
                background: #172B3D;
                color: {TEXT};
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)

        title_column = QVBoxLayout()
        title_column.setSpacing(1)

        title = QLabel("TEST REPORTS")
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 12px; font-weight: 800; letter-spacing: 1px;"
        )

        self.count_label = QLabel("0 FILES")
        self.count_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 700;"
        )

        title_column.addWidget(title)
        title_column.addWidget(self.count_label)
        header.addLayout(title_column)
        header.addStretch()

        add_btn = make_button("+ ADD PDF", primary=True, compact=True)
        add_btn.setMinimumWidth(96)
        header.addWidget(add_btn)
        add_btn.clicked.connect(self._add_pdf)

        root.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(108)
        self.list_widget.setMaximumHeight(155)
        root.addWidget(self.list_widget)

        report_bar = QHBoxLayout()
        report_bar.setSpacing(6)

        self.report_name = QLabel("NO REPORT SELECTED")
        self.report_name.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 700;"
        )
        self.report_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        report_bar.addWidget(self.report_name, 1)

        clear_btn = make_button("CLEAR", compact=True)
        clear_btn.setMinimumWidth(68)
        report_bar.addWidget(clear_btn)
        clear_btn.clicked.connect(self._clear_selection)

        root.addLayout(report_bar)

        if PDF_AVAILABLE:
            self.pdf_view = QPdfView(self)
            # Attach the document before loading. Keeping one long-lived
            # QPdfDocument attached to QPdfView avoids the blank-view
            # regression caused by attaching it only at Status.Ready.
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)
            self.pdf_view.setStyleSheet(
                f"""
                QPdfView {{
                    background: #090D12;
                    border: 1px solid {BORDER};
                    border-radius: 6px;
                }}
                """
            )
            root.addWidget(self.pdf_view, 1)

            self._document = QPdfDocument(self)
            self.pdf_view.setDocument(self._document)
            self._document.statusChanged.connect(self._pdf_status_changed)
            self._show_pdf_placeholder()
        else:
            self.pdf_view = None
            message = QLabel(
                "Qt PDF is not available.\n\n"
                "Install with:\n"
                "pip install PyQt6-Qt6"
            )
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            message.setWordWrap(True)
            message.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 11px;"
            )
            root.addWidget(message, 1)

        summary_title = QLabel("AI-GENERATED SUMMARY — VERIFY AGAINST ORIGINAL REPORT")
        summary_title.setWordWrap(True)
        summary_title.setStyleSheet(f"color: {AMBER}; font-size: 10px; font-weight: 800;")
        root.addWidget(summary_title)
        summary_controls = QHBoxLayout()
        self.summary_status = QLabel("Not generated")
        self.summary_status.setWordWrap(True)
        self.summary_status.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        self.generate_summary_btn = make_button("GENERATE SUMMARY", primary=True, compact=True)
        self.generate_summary_btn.setEnabled(False)
        configuration_error = self.summarizer.configuration_error()
        if configuration_error:
            self.summary_status.setText(configuration_error)
            self.generate_summary_btn.setToolTip(configuration_error)
        summary_controls.addWidget(self.summary_status, 1)
        summary_controls.addWidget(self.generate_summary_btn)
        root.addLayout(summary_controls)
        self.summary_output = QTextEdit()
        self.summary_output.setReadOnly(True)
        self.summary_output.setPlaceholderText("Summary and key findings will appear here.")
        self.summary_output.setMinimumHeight(85)
        self.summary_output.setMaximumHeight(145)
        self.summary_output.setStyleSheet(f"background: {INPUT}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER}; border-radius: 6px; font-size: 10px;")
        root.addWidget(self.summary_output)
        self.generate_summary_btn.clicked.connect(self._generate_summary)

        self.list_widget.itemSelectionChanged.connect(self._view_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._view_selected())

        self._refresh()

    def _show_pdf_placeholder(self):
        if self.pdf_view is None:
            return
        self.pdf_view.setVisible(False)
        self.report_name.setText("SELECT A PDF TO REVIEW")

    def _refresh(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        for path in self.reports:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list_widget.addItem(item)

        self.list_widget.blockSignals(False)
        count = len(self.reports)
        self.count_label.setText(f"{count} FILE" if count == 1 else f"{count} FILES")

        if not self.reports:
            self._clear_selection()
        elif self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)

    def _add_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Test Report",
            str(Path.home()),
            "PDF Files (*.pdf)",
        )
        if not path:
            return

        if path not in self.reports:
            self.reports.append(path)
            self.reports_changed.emit(self.reports)
        self._refresh()

        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                self.list_widget.setCurrentRow(row)
                break

    def _clear_selection(self):
        self._selected_path = None
        self._document_path = None
        self.list_widget.clearSelection()
        self.report_name.setText("SELECT A PDF TO REVIEW")
        self.summary_status.setText("Not generated")
        self.summary_output.clear()
        self.generate_summary_btn.setEnabled(False)
        if self.pdf_view is not None:
            self.pdf_view.setVisible(False)

    def _view_selected(self):
        if self.pdf_view is None:
            return

        item = self.list_widget.currentItem()
        if item is None:
            self._show_pdf_placeholder()
            return

        path = item.data(Qt.ItemDataRole.UserRole)
        if not path or not os.path.isfile(path):
            self.report_name.setText("REPORT FILE NOT FOUND")
            self.pdf_view.setVisible(False)
            QMessageBox.warning(
                self,
                "Report unavailable",
                "The selected PDF could not be found.",
            )
            return

        self.report_name.setText(Path(path).name)
        self._selected_path = path
        configuration_error = self.summarizer.configuration_error()
        self.generate_summary_btn.setEnabled(not self._summary_running)
        self.generate_summary_btn.setToolTip(configuration_error or "Summarize the selected PDF report")
        self.summary_output.setPlainText(self._summaries.get(path, ""))
        if path in self._summaries:
            self.summary_status.setText("Generated for this report")
        elif configuration_error is None:
            self.summary_status.setText("Not generated")
        else:
            self.summary_status.setText("AI summarization is not configured.")

        # Keep QPdfView attached while the document transitions through
        # Loading -> Ready. Detaching/reattaching at Ready can leave the
        # embedded viewer blank on some Qt 6 builds.
        self.pdf_view.setVisible(False)
        self.report_name.setText("LOADING PDF…")

        if self._document is None:
            self._document = QPdfDocument(self)
            self.pdf_view.setDocument(self._document)
            self._document.statusChanged.connect(self._pdf_status_changed)
        else:
            self._document.close()

        self._document_path = path
        error = self._document.load(path)
        if error != QPdfDocument.Error.None_:
            self.report_name.setText(f"UNABLE TO OPEN  •  {error.name}")
            self._document_path = None
            return

        self._pdf_status_changed()

    def _pdf_status_changed(self, *_args):
        if self._document is None or not self._document_path:
            return
        status = self._document.status()
        if status == QPdfDocument.Status.Loading:
            self.report_name.setText("LOADING PDF…")
            return
        if status == QPdfDocument.Status.Error:
            self.report_name.setText("PDF COULD NOT BE RENDERED")
            self.pdf_view.setVisible(False)
            return
        if status != QPdfDocument.Status.Ready:
            return
        self.report_name.setText(Path(self._document_path).name)
        # The document is already attached to QPdfView. Only reveal it when
        # ready; do not detach/re-attach it during the Ready transition.
        self.pdf_view.setVisible(True)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)
        self._start_text_extraction(self._document_path)

    def _start_text_extraction(self, path: str):
        if path in self._extracted_text or path in self._text_tasks:
            return
        task = PdfTextExtractionTask(path)
        task.signals.text_extracted.connect(self._on_text_extracted)
        task.signals.failed.connect(self._on_text_extraction_failed)
        self._text_tasks[path] = task
        if self._selected_path == path:
            self.summary_status.setText("Extracting report text…")
        self._worker_pool.start(task)

    def _on_text_extracted(self, path: str, text: str):
        self._text_tasks.pop(path, None)
        self._extraction_errors.pop(path, None)
        self._extracted_text[path] = text
        if self._selected_path == path:
            self.summary_status.setText("Report text extracted. Ready to summarize.")

    def _on_text_extraction_failed(self, path: str, message: str):
        self._text_tasks.pop(path, None)
        self._extraction_errors[path] = message
        if self._selected_path == path:
            self.summary_status.setText(message)

    def _generate_summary(self):
        path = self._selected_path
        if not path or self._summary_running:
            return
        extracted_text = self._extracted_text.get(path)
        if not extracted_text:
            message = self._extraction_errors.get(path)
            if message:
                self.summary_status.setText(message)
            else:
                self.summary_status.setText("Report text is still being extracted. Please try again shortly.")
            return
        configuration_error = self.summarizer.configuration_error()
        if configuration_error:
            self.summary_status.setText(configuration_error)
            return
        self._summary_running = True
        self.generate_summary_btn.setEnabled(False)
        self.generate_summary_btn.setText("GENERATING…")
        self.summary_status.setText("Generating AI summary…")
        task = ReportSummaryTask(path, extracted_text)
        task.signals.summary_ready.connect(self._summary_completed)
        task.signals.failed.connect(self._summary_failed)
        self._summary_task = task
        self._worker_pool.start(task)

    def _summary_completed(self, path: str, summary: str):
        self._finish_summary_task()
        self._summaries[path] = summary
        if self._selected_path == path:
            self.summary_status.setText("Generated — verify against original report")
            self.summary_output.setPlainText(summary)

    def _summary_failed(self, path: str, message: str):
        self._finish_summary_task()
        if self._selected_path == path:
            self.summary_status.setText(message)
            self.summary_output.clear()

    def _finish_summary_task(self):
        self._summary_running = False
        self._summary_task = None
        self.generate_summary_btn.setText("GENERATE SUMMARY")
        self.generate_summary_btn.setEnabled(bool(self._selected_path))


class CenteredPdfPreview(QWidget):
    """
    Lightweight first-page PDF preview.

    Unlike QPdfView, this widget explicitly calculates the page size
    and places the rendered page in the exact center of the workspace.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.document = None
        self.page_number = 0

        self.setObjectName("CenteredPdfPreview")
        self.setMinimumSize(0, 0)

        self.setStyleSheet(
            f"""
            QWidget#CenteredPdfPreview {{
                background: #090D12;
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        self.page_label = QLabel()
        self.page_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.page_label.setStyleSheet(
            "background: transparent; border: none;"
        )

        root.addWidget(
            self.page_label,
            1,
            Qt.AlignmentFlag.AlignCenter,
        )

        self.info_label = QLabel("")
        self.info_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.info_label.setStyleSheet(
            f"""
            color: {TEXT_MUTED};
            font-size: 7px;
            font-weight: 700;
            background: transparent;
            """
        )

        root.addWidget(
            self.info_label
        )

        self._placeholder = QLabel(
            "PDF VIEWER\n\nLOAD PDF"
        )
        self._placeholder.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self._placeholder.setStyleSheet(
            f"""
            color: {TEXT_MUTED};
            font-size: 15px;
            font-weight: 700;
            background: transparent;
            """
        )

        self._placeholder.show()
        self.page_label.hide()
        self.info_label.hide()

    def set_document(self, document):
        self.document = document
        self.page_number = 0

        if self.document is None:
            self.page_label.hide()
            self.info_label.hide()
            self._placeholder.show()
            return

        self._placeholder.hide()
        self.page_label.show()
        self.info_label.show()

        self._render_page()

    def clear_document(self):
        self.document = None
        self.page_label.clear()
        self.page_label.hide()
        self.info_label.hide()
        self._placeholder.show()

    def _render_page(self):
        if (
            self.document is None
            or self.document.status()
            != QPdfDocument.Status.Ready
        ):
            return

        if self.document.pageCount() <= 0:
            return

        # Actual PDF page dimensions in points.
        page_size = self.document.pagePointSize(
            self.page_number
        )

        if (
            page_size.width() <= 0
            or page_size.height() <= 0
        ):
            return

        # Workspace available for the actual page.
        available_width = max(
            120,
            self.width() - 28,
        )

        available_height = max(
            180,
            self.height() - 42,
        )

        # Preserve the PDF's true aspect ratio.
        scale = min(
            available_width / page_size.width(),
            available_height / page_size.height(),
        )

        target_width = max(
            80,
            int(page_size.width() * scale),
        )

        target_height = max(
            120,
            int(page_size.height() * scale),
        )

        image = self.document.render(
            self.page_number,
            QSize(
                target_width,
                target_height,
            ),
        )

        if image.isNull():
            return

        self.page_label.setPixmap(
            QPixmap.fromImage(image)
        )

        self.info_label.setText(
            f"PAGE {self.page_number + 1} / "
            f"{self.document.pageCount()}"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_page()


class PdfWorkspaceCard(QFrame):
    """Tall centered PDF review workspace for Pre-Op."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        self.title_text = title
        self.pdf_path = None
        self.document = None

        self.setObjectName("PdfWorkspaceCard")

        self.setMinimumWidth(180)

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.setStyleSheet(
            f"""
            QFrame#PdfWorkspaceCard {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(
            10,
            10,
            10,
            10,
        )
        root.setSpacing(7)

        # --------------------------------------------------------
        # Header
        # --------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(5)

        title_label = QLabel(
            title.upper()
        )

        title_label.setStyleSheet(
            f"""
            color: {TEXT};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.7px;
            background: transparent;
            """
        )

        self.status = QLabel(
            "NO PDF"
        )

        self.status.setStyleSheet(
            f"""
            color: {TEXT_MUTED};
            font-size: 7px;
            font-weight: 800;
            background: transparent;
            """
        )

        header.addWidget(
            title_label
        )

        header.addStretch()

        header.addWidget(
            self.status
        )

        root.addLayout(
            header
        )

        # --------------------------------------------------------
        # File name
        # --------------------------------------------------------
        self.file_label = QLabel(
            "No report loaded"
        )

        self.file_label.setWordWrap(
            True
        )

        self.file_label.setStyleSheet(
            f"""
            color: {TEXT_MUTED};
            font-size: 7px;
            font-weight: 600;
            background: transparent;
            """
        )

        root.addWidget(
            self.file_label
        )

        # --------------------------------------------------------
        # Centered PDF preview
        # --------------------------------------------------------
        self.preview = CenteredPdfPreview(
            self
        )

        root.addWidget(
            self.preview,
            1,
        )

        # --------------------------------------------------------
        # Buttons
        # --------------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(5)

        self.load_btn = make_button(
            "LOAD PDF",
            primary=True,
            compact=True,
        )

        self.open_btn = make_button(
            "OPEN",
            compact=True,
        )

        actions.addWidget(
            self.load_btn,
            1,
        )

        actions.addWidget(
            self.open_btn,
            1,
        )

        root.addLayout(
            actions
        )

        self.open_btn.setEnabled(
            False
        )

        self.load_btn.clicked.connect(
            self._load_pdf
        )

        self.open_btn.clicked.connect(
            self._open_pdf
        )

    def _load_pdf(self):
        if not PDF_AVAILABLE:
            QMessageBox.warning(
                self,
                "PDF Support",
                "Qt PDF support is unavailable "
                "in this environment.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            self.title_text,
            str(Path.home()),
            "PDF Files (*.pdf)",
        )

        if not path:
            return

        self.pdf_path = path

        self.file_label.setText(
            Path(path).name
        )

        self.status.setText(
            "LOADING…"
        )

        self.status.setStyleSheet(
            f"""
            color: {AMBER};
            font-size: 7px;
            font-weight: 800;
            background: transparent;
            """
        )

        if self.document is None:
            self.document = QPdfDocument(
                self
            )

            self.document.statusChanged.connect(
                self._pdf_status_changed
            )
        else:
            self.document.close()

        self.preview.clear_document()

        error = self.document.load(
            path
        )

        if error != QPdfDocument.Error.None_:
            self.status.setText(
                f"ERROR • {error.name}"
            )

            self.status.setStyleSheet(
                f"""
                color: {RED};
                font-size: 7px;
                font-weight: 800;
                background: transparent;
                """
            )

            self.open_btn.setEnabled(
                False
            )

            return

        self._pdf_status_changed()

    def _pdf_status_changed(
        self,
        *_args,
    ):
        if (
            self.document is None
            or not self.pdf_path
        ):
            return

        status = self.document.status()

        if status == QPdfDocument.Status.Loading:
            self.status.setText(
                "LOADING…"
            )
            return

        if status == QPdfDocument.Status.Error:
            self.status.setText(
                "PDF ERROR"
            )

            self.status.setStyleSheet(
                f"""
                color: {RED};
                font-size: 7px;
                font-weight: 800;
                background: transparent;
                """
            )

            self.preview.clear_document()

            self.open_btn.setEnabled(
                False
            )

            return

        if status != QPdfDocument.Status.Ready:
            return

        self.preview.set_document(
            self.document
        )

        self.status.setText(
            "READY • REVIEW"
        )

        self.status.setStyleSheet(
            f"""
            color: {GREEN};
            font-size: 7px;
            font-weight: 800;
            background: transparent;
            """
        )

        self.open_btn.setEnabled(
            True
        )

    def _open_pdf(self):
        if (
            self.pdf_path
            and os.path.isfile(
                self.pdf_path
            )
        ):
            PdfViewerDialog(
                self.pdf_path,
                self,
            ).exec()


class PreopPlanningScreen(QWidget):
    """Figma-style Pre-Op workspace with the existing review functionality intact."""

    _PATIENT = {
        "name": "Marcus J. Chen",
        "age": "58",
        "sex": "M",
        "blood": "A+",
        "weight": "82",
        "height": "178",
    }

    _VITALS = [
        ("Heart Rate (HR)", "78", "bpm", GREEN, [75, 76, 78, 79, 78]),
        ("Blood Pressure", "138/84", "mmHg", AMBER, [132, 135, 142, 137, 138]),
        ("Oxygen SpO2", "98", "%", GREEN, [98, 98, 97, 98, 98]),
        ("Temperature", "36.8", "°C", GREEN, [36.7, 36.8, 36.8, 36.8, 36.8]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_index = 0
        self.reports: list[str] = []
        self._annotation_cache: dict[str, list[dict]] = {}

        self.studies = [
            {
                "name": "MRI",
                "path": "assets/medical/mri_sample.png",
            },
            {
                "name": "CT",
                "path": "assets/medical/ct_sample.png",
            },
            {
                "name": "X-RAY",
                "path": None,
            },
            {
                "name": "ECG",
                "path": None,
            },
        ]

        self.cards: dict[str, MedicalImageCard] = {}
        self.viewer: AnnotationFullscreenViewer | None = None
        self.reports_panel: EmbeddedReportsPanel | None = None

        self._build_ui()
        self._load_default_images()

    def _panel(self, object_name: str = "DesignPanel") -> QFrame:
        panel = QFrame()
        panel.setObjectName(object_name)
        panel.setStyleSheet(
            f"""
            QFrame#{object_name} {{
                background: {PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            """
        )
        return panel

    def _label(self, text: str, size: int = 10, color: str = TEXT, weight: int = 500) -> QLabel:
        label = QLabel(text)
        label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        label.setStyleSheet(
            f"background: transparent; color: {color}; font-size: {size}px; font-weight: {weight};"
        )
        return label

    def _section_title(self, text: str) -> QLabel:
        label = self._label(text, 11, TEXT_SECONDARY, 800)
        label.setStyleSheet(
            f"background: transparent; color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 800; letter-spacing: 0.4px;"
        )
        return label

    def _add_divider(self, layout):
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {BORDER};")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)

        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # CENTER: four equal imaging workspaces
        # ------------------------------------------------------------------
        center = QWidget()

        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(
            0, 0, 0, 0
        )
        center_layout.setSpacing(0)

        grid_host = QWidget()

        grid = QGridLayout(grid_host)
        grid.setContentsMargins(
            0, 0, 0, 0
        )
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        for index, study in enumerate(
            self.studies
        ):
            card = MedicalImageCard(
                study["name"],
                study["path"],
                self,
            )

            card.setMinimumSize(0, 0)

            card.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )

            self.cards[
                study["name"]
            ] = card

            card.annotate_requested.connect(
                self._annotate_study
            )

            card.fullscreen_requested.connect(
                self._open_fullscreen
            )

            card.load_requested.connect(
                self._load_study_image
            )

            grid.addWidget(
                card,
                index // 2,
                index % 2,
            )

        self._grid_host = grid_host
        self._grid_layout = grid

        center_layout.addWidget(
            grid_host,
            1,
        )

        # ------------------------------------------------------------------
        # TWO TALL PDF WORKSPACES
        # Positioned between the imaging grid and the existing AI panels.
        # ------------------------------------------------------------------

        self.pdf_workspace_1 = PdfWorkspaceCard(
            "PDF Report 01",
            self,
        )

        self.pdf_workspace_2 = PdfWorkspaceCard(
            "PDF Report 02",
            self,
        )

        content.addWidget(
            center,
            3,
        )

        content.addWidget(
            self.pdf_workspace_1,
            1,
        )

        content.addWidget(
            self.pdf_workspace_2,
            1,
        )

        # ------------------------------------------------------------------
        # RIGHT: clinical summary + assistant
        # ------------------------------------------------------------------
        right = QWidget()
        right.setMinimumWidth(300)
        right.setMaximumWidth(385)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        summary = self._panel("AISummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setSpacing(8)

        summary_header = QHBoxLayout()
        summary_title = self._section_title("⚙  AI Clinical Summary")
        summary_header.addWidget(summary_title)
        summary_header.addStretch()
        conf = QLabel("94% CONF")
        conf.setStyleSheet(
            f"color: {TEXT_SECONDARY}; background: #063548; border-radius: 4px; padding: 3px 6px; font-size: 8px; font-weight: 800;"
        )
        summary_header.addWidget(conf)
        summary_layout.addLayout(summary_header)

        self._add_summary_block(summary_layout, "TUMOR DIMENSIONS", "2.3cm × 1.8cm (Left Temporal Lobe)")
        self._add_summary_block(summary_layout, "ELOQUENT CORTEX PROXIMITY", "4mm from Broca's Area (Critical Margin)", AMBER)
        self._add_summary_block(summary_layout, "ESTIMATED DURATION", "4.5 – 5.0 hours suggested")
        self._add_summary_block(summary_layout, "RISK ASSESSMENT", "High speech-deficit risk. Cortical mapping\nrequired.", RED)
        summary_layout.addStretch(1)
        right_layout.addWidget(summary, 1)

        # ------------------------------------------------------------------
        # RIGHT: interactive AI Surgical Assistant
        # ------------------------------------------------------------------
        assistant = self._panel("AISurgicalAssistant")

        assistant_layout = QVBoxLayout(assistant)
        assistant_layout.setContentsMargins(12, 12, 12, 10)
        assistant_layout.setSpacing(8)

        # Header ------------------------------------------------------------
        assistant_head = QHBoxLayout()
        assistant_head.setSpacing(7)

        dot = QLabel("●")
        dot.setFixedWidth(10)
        dot.setStyleSheet(
            f"color: {GREEN}; font-size: 11px; background: transparent;"
        )
        assistant_head.addWidget(dot)

        assistant_title = self._section_title("AI Surgical Assistant")
        assistant_head.addWidget(assistant_title)
        assistant_head.addStretch()

        online = QLabel("ONLINE")
        online.setAlignment(Qt.AlignmentFlag.AlignCenter)
        online.setStyleSheet(
            f"color: {GREEN}; "
            f"background: rgba(34, 197, 94, 0.10); "
            f"border: 1px solid rgba(34, 197, 94, 0.25); "
            f"border-radius: 5px; "
            f"padding: 3px 7px; "
            f"font-size: 8px; font-weight: 800;"
        )
        assistant_head.addWidget(online)

        new_chat = QToolButton()
        new_chat.setText("↻")
        new_chat.setToolTip("Start a new conversation")
        new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        new_chat.setFixedSize(26, 26)
        new_chat.setStyleSheet(
            f"QToolButton {{ "
            f"background: transparent; "
            f"color: {TEXT_MUTED}; "
            f"border: 1px solid {BORDER}; "
            f"border-radius: 6px; "
            f"font-size: 13px; }}"
            f"QToolButton:hover {{ "
            f"color: {TEXT}; border-color: {BLUE}; }}"
        )
        assistant_head.addWidget(new_chat)

        assistant_layout.addLayout(assistant_head)

        context = QLabel(
            "CASE CONTEXT  •  PRE-OP REVIEW  •  MARISA KÖHLER"
        )
        context.setStyleSheet(
            f"color: {TEXT_MUTED}; "
            f"font-size: 8px; font-weight: 700; "
            f"letter-spacing: 0.45px; "
            f"background: transparent;"
        )
        assistant_layout.addWidget(context)

        # Chat history ------------------------------------------------------
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chat_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._chat_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._chat_scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QScrollBar:vertical {{ "
            f"background: transparent; width: 6px; margin: 2px 0; }}"
            f"QScrollBar::handle:vertical {{ "
            f"background: #334155; border-radius: 3px; min-height: 30px; }}"
            f"QScrollBar::add-line:vertical, "
            f"QScrollBar::sub-line:vertical {{ height: 0; }}"
        )

        chat_host = QWidget()
        chat_host.setStyleSheet("background: transparent;")

        self._chat_feed = QVBoxLayout(chat_host)
        self._chat_feed.setContentsMargins(2, 4, 2, 4)
        self._chat_feed.setSpacing(9)

        self._chat_scroll.setWidget(chat_host)
        assistant_layout.addWidget(self._chat_scroll, 1)

        self._chat_messages = []
        self._chat_typing_widget = None

        self._append_chat_message(
            "assistant",
            "I can help review the pre-op case, imaging findings, "
            "reports, medications, and readiness items."
        )

        self._append_chat_message(
            "user",
            "What is the optimal craniotomy approach given tumor "
            "proximity to Broca’s?"
        )

        self._append_chat_message(
            "assistant",
            "Based on the current case context, review the lesion's "
            "relationship to eloquent language cortex together with "
            "the available imaging and clinical findings before "
            "finalizing the operative approach."
        )

        # Quick actions -----------------------------------------------------
        quick_header = QLabel("QUICK ACTIONS")
        quick_header.setStyleSheet(
            f"color: {TEXT_MUTED}; "
            f"font-size: 8px; font-weight: 800; "
            f"letter-spacing: 0.6px; "
            f"background: transparent;"
        )
        assistant_layout.addWidget(quick_header)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(5)

        quick_actions = [
            ("Summarize case", "Summarize the pre-op case."),
            ("Review imaging", "Review the available imaging context."),
            ("Key risks", "What are the key risks to review before surgery?"),
        ]

        for label_text, prompt in quick_actions:
            button = QPushButton(label_text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(27)
            button.setStyleSheet(
                f"QPushButton {{ "
                f"background: {INPUT}; "
                f"color: {TEXT_SECONDARY}; "
                f"border: 1px solid {BORDER}; "
                f"border-radius: 7px; "
                f"padding: 3px 8px; "
                f"font-size: 8px; font-weight: 700; }}"
                f"QPushButton:hover {{ "
                f"color: {TEXT}; "
                f"border-color: {BLUE}; "
                f"background: #172B3D; }}"
            )
            quick_row.addWidget(button)
            button.clicked.connect(
                lambda _checked=False, p=prompt:
                self._send_chat_prompt(p)
            )

        quick_row.addStretch(1)
        assistant_layout.addLayout(quick_row)

        # Composer ----------------------------------------------------------
        composer = QFrame()
        composer.setObjectName("AssistantComposer")
        composer.setStyleSheet(
            f"QFrame#AssistantComposer {{ "
            f"background: {INPUT}; "
            f"border: 1px solid {BORDER}; "
            f"border-radius: 9px; }}"
        )

        composer_row = QHBoxLayout(composer)
        composer_row.setContentsMargins(7, 4, 5, 4)
        composer_row.setSpacing(5)

        self.assistant_input = QLineEdit()
        self.assistant_input.setPlaceholderText("Ask about this case…")
        self.assistant_input.setMinimumHeight(30)
        self.assistant_input.setStyleSheet(
            f"QLineEdit {{ "
            f"background: transparent; "
            f"color: {TEXT}; "
            f"border: none; "
            f"padding: 0 4px; "
            f"font-size: 9px; }}"
        )

        self._chat_send_button = QToolButton()
        self._chat_send_button.setText("➤")
        self._chat_send_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self._chat_send_button.setFixedSize(30, 30)
        self._chat_send_button.setStyleSheet(
            f"QToolButton {{ "
            f"background: {BLUE}; "
            f"color: #FFFFFF; "
            f"border: none; "
            f"border-radius: 7px; "
            f"font-size: 13px; font-weight: 800; }}"
            f"QToolButton:hover {{ "
            f"background: #2AA8FF; }}"
        )

        composer_row.addWidget(self.assistant_input, 1)
        composer_row.addWidget(self._chat_send_button)

        assistant_layout.addWidget(composer)

        footer = QLabel(
            "AI responses are advisory  •  verify against the clinical record"
        )
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(
            f"color: {TEXT_MUTED}; "
            f"font-size: 7px; "
            f"background: transparent;"
        )
        assistant_layout.addWidget(footer)

        new_chat.clicked.connect(self._reset_chat)
        self.assistant_input.returnPressed.connect(
            self._submit_assistant_question
        )
        self._chat_send_button.clicked.connect(
            self._submit_assistant_question
        )

        right_layout.addWidget(assistant, 1)

        content.addWidget(right, 1)

        root.addLayout(content, 1)

        self.setObjectName("PreopPlanningScreen")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Keep the screen background on the root only.  Child text widgets
        # (QLabel/QWidget containers) must remain transparent so the panel
        # surface shows through instead of producing black rectangles behind
        # every newly added line of text.  Individual controls that need a
        # background define it explicitly in their own stylesheets.
        self.setStyleSheet(
            f"""
            QWidget#PreopPlanningScreen {{
                background: {BG};
                color: {TEXT};
            }}
            QWidget#PreopPlanningScreen QLabel {{
                background: transparent;
                color: {TEXT};
            }}
            QWidget#PreopPlanningScreen QScrollArea {{
                background: transparent;
            }}
            QWidget#PreopPlanningScreen QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QWidget#PreopPlanningScreen > QWidget,
            QWidget#PreopPlanningScreen > QWidget > QWidget {{
                background: transparent;
            }}
            """
        )

    def _add_summary_block(self, layout, heading: str, body: str, body_color: str = TEXT_SECONDARY):
        layout.addWidget(self._label(heading, 8, TEXT_MUTED, 700))
        block = self._label(body, 10, body_color, 600)
        block.setWordWrap(True)
        layout.addWidget(block)

    def _append_chat_message(self, role: str, body: str):
        """Append a left/right conversational chat bubble."""
        is_user = role == "user"

        row_host = QWidget()
        row_host.setStyleSheet("background: transparent;")

        row = QHBoxLayout(row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)

        avatar = QLabel("YOU" if is_user else "AI")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(28, 28)

        if is_user:
            avatar.setStyleSheet(
                f"background: #1F2937; "
                f"color: {TEXT_SECONDARY}; "
                f"border: 1px solid #475569; "
                f"border-radius: 14px; "
                f"font-size: 7px; font-weight: 800;"
            )
        else:
            avatar.setStyleSheet(
                f"background: #063548; "
                f"color: {GREEN}; "
                f"border: 1px solid #0E7490; "
                f"border-radius: 14px; "
                f"font-size: 8px; font-weight: 800;"
            )

        bubble = QFrame()
        bubble.setObjectName(
            "UserBubble" if is_user else "AssistantBubble"
        )
        bubble.setMaximumWidth(305)

        bubble.setStyleSheet(
            f"QFrame#UserBubble {{ "
            f"background: #0B2A3A; "
            f"border: 1px solid #164E63; "
            f"border-radius: 11px; }}"
            f"QFrame#AssistantBubble {{ "
            f"background: #121D31; "
            f"border: 1px solid #263B58; "
            f"border-radius: 11px; }}"
        )

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)

        who = QLabel("YOU" if is_user else "AI SURGICAL ASSISTANT")
        who.setStyleSheet(
            f"color: {TEXT_SECONDARY if is_user else GREEN}; "
            f"font-size: 7px; font-weight: 800; "
            f"letter-spacing: 0.35px; "
            f"background: transparent;"
        )
        bubble_layout.addWidget(who)

        message = QLabel(body)
        message.setWordWrap(True)
        message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        message.setStyleSheet(
            f"color: {TEXT if is_user else TEXT_SECONDARY}; "
            f"font-size: 9px; "
            f"font-weight: 500; "
            f"background: transparent;"
        )
        bubble_layout.addWidget(message)

        if is_user:
            row.addStretch(1)
            row.addWidget(bubble)
            row.addWidget(avatar)
        else:
            row.addWidget(avatar)
            row.addWidget(bubble)
            row.addStretch(1)

        self._chat_feed.addWidget(row_host)
        self._chat_messages.append(row_host)
        self._scroll_chat_to_bottom()

    def _add_chat_bubble(
        self,
        layout,
        speaker: str,
        body: str,
        speaker_color: str = BLUE,
    ):
        """Backward-compatible wrapper for existing code."""
        role = (
            "user"
            if speaker.upper() in ("YOU", "DR. VASQUEZ")
            else "assistant"
        )
        self._append_chat_message(role, body)

    def _scroll_chat_to_bottom(self):
        QTimer.singleShot(
            0,
            lambda:
            self._chat_scroll.verticalScrollBar().setValue(
                self._chat_scroll.verticalScrollBar().maximum()
            ),
        )

    def _show_typing_indicator(self):
        if self._chat_typing_widget is not None:
            return

        row_host = QWidget()
        row_host.setStyleSheet("background: transparent;")

        row = QHBoxLayout(row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)

        avatar = QLabel("AI")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(28, 28)
        avatar.setStyleSheet(
            f"background: #063548; "
            f"color: {GREEN}; "
            f"border: 1px solid #0E7490; "
            f"border-radius: 14px; "
            f"font-size: 8px; font-weight: 800;"
        )

        bubble = QFrame()
        bubble.setStyleSheet(
            f"background: #121D31; "
            f"border: 1px solid #263B58; "
            f"border-radius: 11px;"
        )

        bubble_layout = QHBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(6)

        dots = QLabel("●  ●  ●")
        dots.setStyleSheet(
            f"color: {TEXT_MUTED}; "
            f"font-size: 8px; "
            f"background: transparent;"
        )

        status = QLabel("ANALYZING…")
        status.setStyleSheet(
            f"color: {TEXT_MUTED}; "
            f"font-size: 8px; font-weight: 700; "
            f"background: transparent;"
        )

        bubble_layout.addWidget(dots)
        bubble_layout.addWidget(status)

        row.addWidget(avatar)
        row.addWidget(bubble)
        row.addStretch(1)

        self._chat_feed.addWidget(row_host)
        self._chat_typing_widget = row_host
        self._scroll_chat_to_bottom()

    def _remove_typing_indicator(self):
        widget = self._chat_typing_widget
        self._chat_typing_widget = None

        if widget is not None:
            self._chat_feed.removeWidget(widget)
            widget.deleteLater()

    def _send_chat_prompt(self, prompt: str):
        self.assistant_input.setText(prompt)
        self._submit_assistant_question()

    def _submit_assistant_question(self):
        question = self.assistant_input.text().strip()

        if not question:
            return

        self.assistant_input.clear()
        self._append_chat_message("user", question)

        self._show_typing_indicator()

        self._chat_send_button.setEnabled(False)
        self.assistant_input.setEnabled(False)

        QTimer.singleShot(
            650,
            lambda q=question: self._finish_chat_response(q),
        )

    def _finish_chat_response(self, question: str):
        self._remove_typing_indicator()

        q = question.lower()

        if "summarize" in q or "case" in q:
            response = (
                "I can summarize the available pre-op context into "
                "key findings, imaging observations, reports, "
                "medications, and readiness items. In this prototype, "
                "the response uses the information currently visible "
                "in the console."
            )
        elif (
            "imaging" in q
            or "mri" in q
            or "ct" in q
            or "x-ray" in q
        ):
            response = (
                "The imaging workspace is the primary source for image "
                "review. Open the relevant MRI, CT, X-Ray, or Clinical "
                "study to inspect and annotate it before proceeding."
            )
        elif "risk" in q:
            response = (
                "Review the documented diagnosis, allergies, active "
                "medications, current vitals, imaging, reports, and "
                "outstanding readiness items together. Verify any "
                "surgical-risk assessment against the clinical record."
            )
        else:
            response = (
                "I’m tracking this as a pre-op question. Review the "
                "case context, imaging, reports, and readiness status "
                "shown in the console before making the operative decision."
            )

        self._append_chat_message("assistant", response)

        self._chat_send_button.setEnabled(True)
        self.assistant_input.setEnabled(True)
        self.assistant_input.setFocus()

    def _reset_chat(self):
        for widget in getattr(self, "_chat_messages", []):
            self._chat_feed.removeWidget(widget)
            widget.deleteLater()

        self._chat_messages = []
        self._remove_typing_indicator()

        self._append_chat_message(
            "assistant",
            "New conversation started. Ask me about the current "
            "pre-op case."
        )

        self.assistant_input.clear()
        self.assistant_input.setEnabled(True)
        self._chat_send_button.setEnabled(True)

    def _compare_cases(self):
        QMessageBox.information(self, "Compare Cases", "Case comparison is available through the existing Pre-Op workflow.")

    def _summarize_risks(self):
        QMessageBox.information(self, "Summarize Risks", "Risk summary is shown in the AI Clinical Summary panel.")

    def _add_report_from_strip(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Test Report",
            str(Path.home()),
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        if path not in self.reports:
            self.reports.append(path)
        self._reports_changed(self.reports)

    def _open_report_workspace(self, selected_path: str | None = None):
        dialog = ReportReviewDialog(self.reports, self)
        self.reports_panel = dialog.panel
        dialog.panel.reports_changed.connect(self._reports_changed)
        if selected_path and selected_path in self.reports:
            try:
                row = self.reports.index(selected_path)
                dialog.panel.list_widget.setCurrentRow(row)
            except ValueError:
                pass
        dialog.exec()
        self.reports_panel = None

    def _make_report_card(self, path: str) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(132, 96)
        name = Path(path).stem or Path(path).name
        if len(name) > 16:
            name = name[:15] + "…"
        button.setText(f"▣  {name}\n\nPDF  •  ATTACHED\nCLICK TO REVIEW")
        button.setToolTip(Path(path).name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"""
            QPushButton {{
                text-align: left;
                background: {CARD};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 7px;
                font-size: 8px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                border-color: {BLUE};
                background: #1A222D;
            }}
            """
        )
        button.clicked.connect(lambda _checked=False, p=path: self._open_report_workspace(p))
        return button

    def _refresh_report_strip(self):
        while self.report_cards_layout.count():
            item = self.report_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for path in self.reports[:8]:
            self.report_cards_layout.addWidget(self._make_report_card(path))

        if len(self.reports) == 0:
            empty = self._label("No reports attached", 8, TEXT_MUTED, 600)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.report_cards_layout.addWidget(empty, 1)
        else:
            self.report_cards_layout.addStretch(1)

        count = len(self.reports)
        self.report_count_label.setText(
            f"{count} FILE ATTACHED" if count == 1 else f"{count} FILES ATTACHED"
        )

    def _reports_changed(self, reports):
        self.reports = list(reports)

    def _load_default_images(self):
        base = Path(__file__).resolve().parent.parent
        for study in self.studies:
            path = study["path"]
            if path:
                self.cards[study["name"]].set_image(str(base / path))

    def _load_study_image(self, study_name: str):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Load {study_name} Image",
            str(Path.home()),
            "Medical Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.dcm *.dicom);;"
            "DICOM (*.dcm *.dicom);;"
            "All Files (*)",
        )
        if not path:
            return

        card = self.cards[study_name]
        if card.image_path:
            self._annotation_cache[card.image_path] = copy.deepcopy(card.annotations)

        if Path(path).suffix.lower() in (".dcm", ".dicom"):
            frames, error = dicom_frame_pixmaps(path)
            if error:
                QMessageBox.warning(self, "Unable to load DICOM", error)
                return
            card.set_dicom_frames(path, frames)
            card.set_annotations(self._annotation_cache.get(path, []))
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.information(
                self,
                "Image format not supported",
                "This image could not be decoded. Choose a supported PNG, JPG, TIFF, BMP, or DICOM image.",
            )
            return

        card.set_image(path)
        card.set_annotations(self._annotation_cache.get(path, []))

    def _annotate_study(self, study_name: str):
        self._open_fullscreen(study_name)

    def _open_fullscreen(self, study_name: str):
        card = self.cards.get(study_name)
        if card is None or card.pixmap.isNull():
            QMessageBox.information(self, "No image", f"No {study_name} image is loaded yet.")
            return

        self.viewer = AnnotationFullscreenViewer(
            study_name,
            card.pixmap,
            card.annotations,
            self,
            card.frames if card.frames else [card.pixmap],
            card.frame_index,
        )
        self.viewer.annotations_changed.connect(
            lambda annotations, name=study_name: self._save_annotations(name, annotations)
        )
        self.viewer.showFullScreen()
        self.viewer.raise_()
        self.viewer.activateWindow()

    def _save_annotations(self, study_name: str, annotations: list[dict]):
        card = self.cards.get(study_name)
        if card is not None:
            card.set_annotations(annotations)
            if card.image_path:
                self._annotation_cache[card.image_path] = copy.deepcopy(annotations)

    def _navigate(self, direction: int):
        self.current_index = (self.current_index + direction) % len(self.studies)
        name = self.studies[self.current_index]["name"]
        card = self.cards.get(name)
        if card:
            card.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Right:
            self._navigate(1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Left:
            self._navigate(-1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.viewer is not None:
            self.viewer.close()
            event.accept()
            return
        super().keyPressEvent(event)

