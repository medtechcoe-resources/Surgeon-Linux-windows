from __future__ import annotations

import copy
import os
from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QPointF,
    QRectF,
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
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtPdf import QPdfDocument
    from PyQt6.QtPdfWidgets import QPdfView

    PDF_AVAILABLE = True
except ImportError:
    QPdfDocument = None
    QPdfView = None
    PDF_AVAILABLE = False


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

        if self.tool in ("pen", "marker"):
            if self._current_stroke is not None:
                points = self._current_stroke["points"]
                if len(points) >= 2:
                    self.annotations.append(copy.deepcopy(self._current_stroke))

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

        self._drawing = False
        self._current_stroke = None
        self._current_item = None

        self._rebuild_scene()
        self.annotations_changed.emit(copy.deepcopy(self.annotations))
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
    ):
        super().__init__(parent)

        self.title = title
        self._closed = False

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
        self.canvas.set_content(pixmap, annotations)
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
    """Compact 2x2 gallery card for MRI, CT, X-Ray and Clinical images."""

    fullscreen_requested = pyqtSignal(str)
    annotate_requested = pyqtSignal(str)
    load_requested = pyqtSignal(str)

    def __init__(self, study_name: str, image_path: str | None = None, parent=None):
        super().__init__(parent)

        self.study_name = study_name
        self.image_path = image_path
        self.pixmap = QPixmap()
        self.annotations: list[dict] = []

        self.setObjectName("MedicalImageCard")
        # Controlled clinical card proportions. The grid supplies the width,
        # while the height is deliberately fixed so cards never stretch into
        # oversized horizontal or vertical panels.
        self.setMinimumSize(0, 0)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(7)

        name = QLabel(study_name.upper())
        name.setStyleSheet(
            f"""
            QLabel {{
                color: {TEXT};
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
            """
        )

        self.status = QLabel("NO IMAGE AVAILABLE")
        self.status.setStyleSheet(
            f"""
            QLabel {{
                color: {TEXT_MUTED};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.6px;
            }}
            """
        )

        header.addWidget(name)
        header.addStretch()
        header.addWidget(self.status)

        layout.addLayout(header)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Critical grid fix: QLabel uses the pixmap dimensions as its sizeHint.
        # When MRI/CT load a large pixmap, that sizeHint can force the first
        # grid row to become enormous and squeeze X-RAY/CLINICAL. Ignored tells
        # the layout to size the preview from the grid cell, not the image.
        self.image_label.setMinimumSize(0, 0)
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored,
        )
        self.image_label.setStyleSheet(
            f"""
            QLabel {{
                background: #090D12;
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            """
        )
        layout.addWidget(self.image_label, 1)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        self.annotate_btn = make_button("ANNOTATE", primary=True, compact=True)
        self.fullscreen_btn = make_button("FULL SCREEN", compact=True)
        self.load_btn = make_button("LOAD IMAGE", compact=True)

        controls.addWidget(self.annotate_btn)
        controls.addWidget(self.fullscreen_btn)
        controls.addStretch()
        controls.addWidget(self.load_btn)

        layout.addLayout(controls)

        self.annotate_btn.clicked.connect(
            lambda: self.annotate_requested.emit(self.study_name)
        )
        self.fullscreen_btn.clicked.connect(
            lambda: self.fullscreen_requested.emit(self.study_name)
        )
        self.load_btn.clicked.connect(
            lambda: self.load_requested.emit(self.study_name)
        )

        self.setStyleSheet(
            f"""
            QFrame#MedicalImageCard {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QFrame#MedicalImageCard:hover {{
                border: 1px solid #334155;
            }}
            """
        )

        self.set_image(image_path)

    def set_image(self, image_path: str | None):
        self.image_path = image_path

        if image_path and os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                self.pixmap = pixmap
                self.status.setText("IMAGE LOADED")
                self.status.setStyleSheet(
                    f"color: {GREEN}; font-size: 9px; font-weight: 700;"
                )
                self._update_preview()
                return

        self.pixmap = QPixmap()
        self.status.setText("NO IMAGE AVAILABLE")
        self.status.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 700;"
        )

        self.image_label.setText(
            "NO IMAGE\n\n"
            "Load a study image to review it here"
        )
        self.image_label.setStyleSheet(
            f"""
            QLabel {{
                background: #090D12;
                color: {TEXT_MUTED};
                border: 1px dashed {BORDER_HEAVY};
                border-radius: 6px;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            """
        )

    def set_annotations(self, annotations: list[dict]):
        self.annotations = copy.deepcopy(annotations)
        if not self.pixmap.isNull():
            self._update_preview()

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

        self.image_label.setStyleSheet(
            f"""
            QLabel {{
                background: #090D12;
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            """
        )
        self.image_label.setPixmap(canvas)

        if self.annotations:
            self.status.setText(f"ANNOTATED  •  {len(self.annotations)}")
            self.status.setStyleSheet(
                f"color: {AMBER}; font-size: 9px; font-weight: 700;"
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.pixmap.isNull():
            self._update_preview()


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

        self.pdf_view = QPdfView(self)
        self.pdf_view.setDocument(self.document)

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

        layout.addWidget(self.pdf_view, 1)


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
        self.list_widget.clear()

        if not self.reports:
            empty = QListWidgetItem("NO REPORTS LOADED")
            empty.setForeground(QColor(TEXT_MUTED))
            self.list_widget.addItem(empty)
            return

        for path in self.reports:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list_widget.addItem(item)

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
        self.list_widget.clearSelection()
        self.report_name.setText("SELECT A PDF TO REVIEW")
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

        if self._document is None:
            self._document = QPdfDocument(self)
        else:
            self._document.close()

        error = self._document.load(path)
        if error != QPdfDocument.Error.None_:
            self.report_name.setText(f"UNABLE TO OPEN  •  {error.name}")
            self.pdf_view.setVisible(False)
            return

        self.pdf_view.setDocument(self._document)
        self.pdf_view.setVisible(True)
        # QPdfView does not expose a fitInView() method like QGraphicsView.
        # Its FitInView zoom mode performs the fitting automatically.
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)


class PreopPlanningScreen(QWidget):
    """Surgeon-first Pre-Op workspace with imaging and embedded reports.

    The workspace is intentionally responsive: the 2x2 imaging grid and the
    embedded report viewer share the available Pre-Op area without fixed pixel
    dimensions, so the layout remains stable across display scaling settings.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_index = 0
        self.reports: list[str] = []

        self.studies = [
            {"name": "MRI", "path": "assets/medical/mri_sample.png"},
            {"name": "CT", "path": "assets/medical/ct_sample.png"},
            {"name": "X-RAY", "path": None},
            {"name": "CLINICAL", "path": None},
        ]

        self.cards: dict[str, MedicalImageCard] = {}
        self.viewer: AnnotationFullscreenViewer | None = None

        self._build_ui()
        self._load_default_images()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Compact heading so the imaging/report workspace gets the majority
        # of the available Pre-Op height.
        header = QHBoxLayout()
        header.setSpacing(8)

        title_column = QVBoxLayout()
        title_column.setSpacing(1)

        title = QLabel("PRE-OPERATIVE IMAGING")
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 16px; font-weight: 800; letter-spacing: 1px;"
        )

        subtitle = QLabel(
            "MRI / CT / X-RAY / CLINICAL  •  REVIEW, ANNOTATE, RETURN"
        )
        subtitle.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; letter-spacing: 0.6px;"
        )

        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        header.addLayout(title_column)
        header.addStretch()

        report_status = QLabel("EMBEDDED REPORT REVIEW")
        report_status.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.5px;"
        )
        header.addWidget(report_status)
        root.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {BORDER};")
        root.addWidget(line)

        # Responsive workstation area.  The grid gets about three quarters
        # of the width and the report viewer gets the remaining quarter.
        # Neither side is given a fixed pixel size.
        workspace = QWidget()
        workspace.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(12)

        grid_host = QWidget()
        grid_host.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnMinimumWidth(0, 0)
        grid.setColumnMinimumWidth(1, 0)
        grid.setRowMinimumHeight(0, 0)
        grid.setRowMinimumHeight(1, 0)

        for index, study in enumerate(self.studies):
            card = MedicalImageCard(study["name"], study["path"], self)
            card.setMinimumSize(0, 0)
            card.setMaximumSize(16777215, 16777215)
            card.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            self.cards[study["name"]] = card

            card.annotate_requested.connect(self._annotate_study)
            card.fullscreen_requested.connect(self._open_fullscreen)
            card.load_requested.connect(self._load_study_image)

            grid.addWidget(card, index // 2, index % 2)

        self._grid_host = grid_host
        self._grid_layout = grid

        reports_panel = EmbeddedReportsPanel(self.reports, self)
        reports_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        reports_panel.setMinimumWidth(280)
        reports_panel.setMaximumWidth(480)
        reports_panel.reports_changed.connect(self._reports_changed)
        self.reports_panel = reports_panel

        workspace_layout.addWidget(grid_host, 3)
        workspace_layout.addWidget(reports_panel, 1)

        # This stretch is the important part: the workspace consumes the
        # entire available Pre-Op content area instead of leaving a large
        # fixed-size block floating in the middle of the page.
        root.addWidget(workspace, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        prev = make_button("←", compact=True)
        nxt = make_button("→", compact=True)
        prev.setMinimumWidth(42)
        nxt.setMinimumWidth(42)

        self.selection_label = QLabel()
        self.selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selection_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10px; font-weight: 700;"
        )

        keyboard_hint = QLabel("ARROW KEYS  •  navigate studies")
        keyboard_hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9px;")

        bottom.addWidget(prev)
        bottom.addWidget(nxt)
        bottom.addWidget(self.selection_label)
        bottom.addStretch()
        bottom.addWidget(keyboard_hint)
        root.addLayout(bottom)

        prev.clicked.connect(lambda: self._navigate(-1))
        nxt.clicked.connect(lambda: self._navigate(1))

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setStyleSheet(
            f"""
            QWidget {{
                background: {BG};
                color: {TEXT};
            }}
            """
        )
        self._update_selection()

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
            "Medical Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;"
            "DICOM (*.dcm);;"
            "All Files (*)",
        )
        if not path:
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.information(
                self,
                "Image format not supported",
                "This Pre-Op viewer currently displays PNG/JPG/TIFF/BMP images.\n\n"
                "DICOM is kept in the file filter for future medical-image integration.",
            )
            return

        self.cards[study_name].set_image(path)

    def _annotate_study(self, study_name: str):
        self._open_fullscreen(study_name)

    def _open_fullscreen(self, study_name: str):
        card = self.cards.get(study_name)
        if card is None or card.pixmap.isNull():
            QMessageBox.information(
                self,
                "No image",
                f"No {study_name} image is loaded yet.",
            )
            return

        self.viewer = AnnotationFullscreenViewer(
            study_name,
            card.pixmap,
            card.annotations,
            self,
        )
        self.viewer.annotations_changed.connect(
            lambda annotations, name=study_name: self._save_annotations(
                name,
                annotations,
            )
        )
        self.viewer.showFullScreen()
        self.viewer.raise_()
        self.viewer.activateWindow()

    def _save_annotations(self, study_name: str, annotations: list[dict]):
        card = self.cards.get(study_name)
        if card is not None:
            card.set_annotations(annotations)

    def _navigate(self, direction: int):
        self.current_index = (self.current_index + direction) % len(self.studies)
        self._update_selection()
        name = self.studies[self.current_index]["name"]
        card = self.cards.get(name)
        if card:
            card.setFocus()

    def _update_selection(self):
        study = self.studies[self.current_index]
        self.selection_label.setText(
            f"{study['name']}   •   {self.current_index + 1}/{len(self.studies)}"
        )

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
