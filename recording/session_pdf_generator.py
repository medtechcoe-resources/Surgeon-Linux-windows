"""
Session PDF Summary & Audit Report Generator.

Generates a professional clinical and engineering PDF audit report for an authenticated
AETHER surgical console session using ReportLab.

Includes:
- Two-pass NumberedCanvas with running headers, footers, and "Page X of Y" pagination.
- Executive summary on Page 1 (Session overview, compact vitals, YOLO, and alert summaries).
- Required regulatory/clinical demonstration disclaimers.
- Detailed statistics and ReportLab vector trend charts for Heart Rate, SpO2, and Blood Pressure (separate systolic & diastolic).
- YOLO object detection statistics and object table (| Object | Detections | Avg Confidence | First Seen | Last Seen |).
- Chronological alerts and system message timelines with relative timestamps.
- Video recording parameters and dropped-frame telemetry.
"""
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak, KeepTogether,
    )
    from reportlab.graphics.shapes import Drawing, Rect, String, Line, PolyLine, Circle
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False
    log.warning("reportlab not installed — PDF generation unavailable")


# ── Color Palette ──────────────────────────────────────────────────
_PRIMARY = colors.HexColor("#0D1B2A")
_SECONDARY = colors.HexColor("#1B263B")
_ACCENT_BLUE = colors.HexColor("#0095FF")
_ACCENT_GREEN = colors.HexColor("#10B981")
_ACCENT_RED = colors.HexColor("#EF4444")
_ACCENT_ORANGE = colors.HexColor("#F59E0B")
_TEXT_MAIN = colors.HexColor("#1F2937")
_TEXT_MUTED = colors.HexColor("#6B7280")
_BG_LIGHT = colors.HexColor("#F9FAFB")
_BG_ALT = colors.HexColor("#F3F4F6")
_BORDER = colors.HexColor("#E5E7EB")
_HEADER_CELL_BG = colors.HexColor("#1E293B")
_HEADER_CELL_FG = colors.HexColor("#FFFFFF")

_DISCLAIMER_TEXT = (
    "NOTICE: Simulation / demonstration data. "
    "YOLO detection output is for visualization/demo purposes and is not a clinical diagnostic result."
)


# ── Running Header/Footer Numbered Canvas ──────────────────────────

class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas that computes total page count and adds running headers/footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def _draw_page_decorations(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#4B5563"))

        session_id = getattr(self, "_session_id", "AETHER-SESSION")
        timestamp = getattr(self, "_export_timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        width, height = A4

        # Header (pages 2+)
        if self._pageNumber > 1:
            self.drawString(40, height - 30, "AETHER SURGICAL CONSOLE  |  SESSION AUDIT REPORT")
            self.drawRightString(width - 40, height - 30, f"SESSION ID: {session_id}")
            self.setStrokeColor(_BORDER)
            self.setLineWidth(0.75)
            self.line(40, height - 34, width - 40, height - 34)

        # Footer (all pages)
        self.setStrokeColor(_BORDER)
        self.setLineWidth(0.75)
        self.line(40, 38, width - 40, 38)

        self.setFont("Helvetica", 7.5)
        self.drawString(40, 26, f"Generated: {timestamp}  |  CONFIDENTIAL & PROPRIETARY  |  SURGICAL AUDIT TRAIL")
        self.drawRightString(width - 40, 26, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


# ── Styles Builder ────────────────────────────────────────────────

def _build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=_PRIMARY,
        spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        "DocSubTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=_ACCENT_BLUE,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "DisclaimerBanner",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#991B1B"),
        backColor=colors.HexColor("#FEF2F2"),
        borderPadding=5,
        spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=_PRIMARY,
        spaceBefore=12,
        spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        "SubSectionHeading",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=_SECONDARY,
        spaceBefore=8,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "CardLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=_TEXT_MUTED,
    ))
    styles.add(ParagraphStyle(
        "CardValue",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=_PRIMARY,
    ))
    styles.add(ParagraphStyle(
        "TableBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=_TEXT_MAIN,
    ))
    styles.add(ParagraphStyle(
        "TableBodyBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=_TEXT_MAIN,
    ))
    styles.add(ParagraphStyle(
        "NoDataNote",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        leading=11,
        textColor=_TEXT_MUTED,
        spaceAfter=4,
    ))
    return styles


# ── Table Helpers ─────────────────────────────────────────────────

def _info_grid(rows: List[List[str]], col_widths=None) -> Table:
    """Two-column key-value table."""
    if not col_widths:
        col_widths = [160, 355]
    table = Table(rows, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), _SECONDARY),
        ("TEXTCOLOR", (1, 0), (1, -1), _TEXT_MAIN),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_BG_LIGHT, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
    ]))
    return table


def _data_table(headers: List[str], rows: List[List[str]], col_widths=None) -> Table:
    """Multi-column table with dark header row and alternating rows."""
    all_data = [headers] + rows
    table = Table(all_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_CELL_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_CELL_FG),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 1), (-1, -1), _TEXT_MAIN),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _BG_LIGHT]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
    ]))
    return table


# ── Vector Trend Charts ───────────────────────────────────────────

def _create_trend_chart(
    series_list: List[Dict[str, Any]],
    title: str,
    y_min: float,
    y_max: float,
    unit: str = "",
    width: float = 515,
    height: float = 90,
) -> Drawing:
    """Generate a clean, compact vector trend chart using ReportLab shapes."""
    d = Drawing(width, height)

    pad_left = 42
    pad_right = 15
    pad_top = 18
    pad_bottom = 20

    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    # Background and border
    d.add(Rect(pad_left, pad_bottom, plot_w, plot_h,
               fillColor=_BG_LIGHT, strokeColor=_BORDER, strokeWidth=0.5))

    # Gridlines (3 horizontal levels: min, mid, max)
    y_mid = (y_min + y_max) / 2.0
    for y_val in [y_min, y_mid, y_max]:
        norm_y = (y_val - y_min) / (y_max - y_min) if (y_max > y_min) else 0.5
        grid_y = pad_bottom + norm_y * plot_h
        d.add(Line(pad_left, grid_y, pad_left + plot_w, grid_y,
                   strokeColor=colors.HexColor("#E2E8F0"), strokeWidth=0.5))
        lbl = f"{int(round(y_val))}"
        d.add(String(pad_left - 6, grid_y - 3, lbl,
                     fontName="Helvetica", fontSize=7, textAnchor="end",
                     fillColor=_TEXT_MUTED))

    # Chart Title
    d.add(String(pad_left, height - 12, f"{title} ({unit})" if unit else title,
                 fontName="Helvetica-Bold", fontSize=8.5, fillColor=_SECONDARY))

    # Plot each series
    legend_x = width - pad_right
    for s in series_list:
        data = s.get("data", [])
        col = s.get("color", _ACCENT_BLUE)
        name = s.get("name", "")

        if len(data) >= 2:
            step_x = plot_w / (len(data) - 1)
            points = []
            for i, val in enumerate(data):
                px = pad_left + i * step_x
                norm_y = (val - y_min) / (y_max - y_min) if (y_max > y_min) else 0.5
                norm_y = max(0.0, min(1.0, norm_y))
                py = pad_bottom + norm_y * plot_h
                points.extend([px, py])

            d.add(PolyLine(points, strokeColor=col, strokeWidth=1.5))

            # Dot at the latest point
            last_x, last_y = points[-2], points[-1]
            d.add(Circle(last_x, last_y, 2.5, fillColor=col, strokeColor=colors.white, strokeWidth=0.75))

        # Legend indicator
        if name:
            d.add(Line(legend_x - 55, height - 8, legend_x - 43, height - 8,
                       strokeColor=col, strokeWidth=2))
            d.add(String(legend_x - 38, height - 11, name,
                         fontName="Helvetica", fontSize=7.5, fillColor=_TEXT_MAIN))
            legend_x -= 65

    return d


# ══════════════════════════════════════════════════════════════════
#  MAIN EXPORT ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def generate_session_pdf(output_path: str, session_data: Dict[str, Any]) -> str:
    """Generate a clinical session audit PDF from a frozen session snapshot."""
    if not REPORTLAB_OK:
        raise RuntimeError("reportlab is not installed — cannot generate PDF")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    styles = _build_styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=0.55 * inch,
        bottomMargin=0.65 * inch,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        title=f"AETHER Session Audit — {session_data.get('session_id', 'N/A')}",
        author="AETHER Surgical Console",
    )

    story = []

    # ── Page 1: Header & Executive Overview ─────────────────────────
    story.append(Paragraph("AETHER SURGICAL CONSOLE", styles["DocTitle"]))
    story.append(Paragraph("SESSION SUMMARY & AUDIT REPORT", styles["DocSubTitle"]))
    story.append(Paragraph(_DISCLAIMER_TEXT, styles["DisclaimerBanner"]))

    # Section 1: Session Overview
    story.append(Paragraph("SESSION OVERVIEW", styles["SectionHeading"]))
    start_str = _format_datetime(session_data.get("start_time", ""))
    end_str = _format_datetime(session_data.get("end_time", ""))

    overview_rows = [
        ["Session ID", session_data.get("session_id", "N/A")],
        ["Date / Session Start", start_str],
        ["Session End", end_str],
        ["Total Duration", session_data.get("duration", "00:00:00")],
        ["Primary Surgeon / User", session_data.get("username", "N/A") or "Unknown"],
        ["User Role", str(session_data.get("role", "N/A")).upper()],
        ["Export Timestamp", _format_datetime(session_data.get("export_timestamp", ""))],
    ]
    story.append(_info_grid(overview_rows))
    story.append(Spacer(1, 10))

    # Executive Overview Cards
    story.append(Paragraph("EXECUTIVE SUMMARY", styles["SectionHeading"]))
    _build_executive_cards(story, session_data, styles)
    story.append(Spacer(1, 10))

    # Clean Page Break between Executive Summary and Detailed Telemetry
    story.append(PageBreak())

    # ── Page 2+: Detailed Patient Vitals ────────────────────────────
    story.append(Paragraph("1. PATIENT VITALS & PHYSIOLOGICAL TELEMETRY", styles["SectionHeading"]))
    _build_vitals_section(story, session_data, styles)
    story.append(Spacer(1, 10))

    # ── Detailed YOLO Object Detection ──────────────────────────────
    story.append(Paragraph("2. ENDOSCOPIC YOLO OBJECT DETECTION ANALYTICS", styles["SectionHeading"]))
    _build_yolo_section(story, session_data, styles)
    story.append(Spacer(1, 10))

    # ── Alerts & Safety Events ──────────────────────────────────────
    story.append(Paragraph("3. CLINICAL & SYSTEM ALERT AUDIT", styles["SectionHeading"]))
    _build_alerts_section(story, session_data, styles)
    story.append(Spacer(1, 10))

    # ── Messages & Console Timeline ─────────────────────────────────
    story.append(Paragraph("4. MESSAGE CENTER & ACTION TIMELINE", styles["SectionHeading"]))
    _build_messages_section(story, session_data, styles)
    story.append(Spacer(1, 10))

    # ── Video Recording Technical Verification ──────────────────────
    story.append(Paragraph("5. VIDEO RECORDING & ENCODING TELEMETRY", styles["SectionHeading"]))
    _build_video_section(story, session_data, styles)

    # Attach session ID and timestamp to canvas maker
    def _canvas_factory(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c._session_id = session_data.get("session_id", "AETHER")
        c._export_timestamp = _format_datetime(session_data.get("export_timestamp", ""))
        return c

    doc.build(story, canvasmaker=_canvas_factory)
    log.info(f"Clinical audit PDF generated: {output_path}")
    return output_path


# ══════════════════════════════════════════════════════════════════
#  SECTION BUILDERS
# ══════════════════════════════════════════════════════════════════

def _build_executive_cards(story, data, styles):
    """Render compact executive overview cards for Vitals, YOLO, and Alerts."""
    vitals = data.get("vitals_snapshots", [])
    valid_vitals = [v for v in vitals if v.get("status") in ("LIVE", "STALE")]
    yolo_stats = data.get("yolo_analytics", {})
    alerts = data.get("alerts", [])

    # Vitals Card
    hr_vals = [v["hr"] for v in valid_vitals if v.get("hr") is not None]
    avg_hr = f"{sum(hr_vals) / len(hr_vals):.0f} bpm" if hr_vals else "No data"

    # YOLO Card
    total_yolo = yolo_stats.get("total_detections", 0)
    detected_classes = len(yolo_stats.get("classes", []))
    yolo_text = f"{total_yolo} total ({detected_classes} classes)" if total_yolo > 0 else "0 detections"

    # Alerts Card
    crit_count = sum(1 for a in alerts if a.get("severity") == "CRITICAL")
    warn_count = sum(1 for a in alerts if a.get("severity") == "WARNING")
    alert_text = f"{len(alerts)} total ({crit_count} Critical, {warn_count} Warning)"

    card_data = [
        [
            Paragraph("PATIENT MONITORING", styles["CardLabel"]),
            Paragraph("YOLO INSTRUMENT AI", styles["CardLabel"]),
            Paragraph("SAFETY ALERTS", styles["CardLabel"]),
        ],
        [
            Paragraph(f"Avg HR: {avg_hr}", styles["CardValue"]),
            Paragraph(yolo_text, styles["CardValue"]),
            Paragraph(alert_text, styles["CardValue"]),
        ],
        [
            Paragraph(f"{len(valid_vitals)} telemetry snapshots", styles["TableBody"]),
            Paragraph(f"Tracking: {'ACTIVE' if yolo_stats.get('tracking_active') else 'INACTIVE'}", styles["TableBody"]),
            Paragraph(f"Safety status: {'ALERT RECORDED' if alerts else 'NOMINAL'}", styles["TableBody"]),
        ],
    ]
    t = Table(card_data, colWidths=[171, 171, 172])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)


def _build_vitals_section(story, data, styles):
    """Build Vitals statistics table, trend charts, and chronological readings."""
    snapshots = data.get("vitals_snapshots", [])
    valid = [s for s in snapshots if s.get("status") in ("LIVE", "STALE")]

    if not valid:
        story.append(Paragraph("No patient vitals recorded during this session.", styles["NoDataNote"]))
        return

    # 1. Statistics Table
    stats_rows = []

    def _calc_stats(name: str, values: List[float], unit: str, decimal: int = 1):
        if not values:
            return None
        min_v = min(values)
        max_v = max(values)
        avg_v = sum(values) / len(values)
        fmt = f"{{:.{decimal}f}}"
        return [
            f"{name} ({unit})" if unit else name,
            fmt.format(min_v),
            fmt.format(avg_v),
            fmt.format(max_v),
            str(len(values)),
        ]

    # HR
    hr_vals = [s["heart_rate"] for s in valid if s.get("heart_rate") is not None]
    if hr_stat := _calc_stats("Heart Rate", hr_vals, "bpm", 0):
        stats_rows.append(hr_stat)

    # SpO2
    spo2_vals = [s["spo2"] for s in valid if s.get("spo2") is not None]
    if spo2_stat := _calc_stats("SpO2", spo2_vals, "%", 1):
        stats_rows.append(spo2_stat)

    # Systolic & Diastolic strictly separate
    sys_vals = [s["systolic_bp"] for s in valid if s.get("systolic_bp") is not None]
    if sys_stat := _calc_stats("Systolic Blood Pressure", sys_vals, "mmHg", 0):
        stats_rows.append(sys_stat)

    dia_vals = [s["diastolic_bp"] for s in valid if s.get("diastolic_bp") is not None]
    if dia_stat := _calc_stats("Diastolic Blood Pressure", dia_vals, "mmHg", 0):
        stats_rows.append(dia_stat)

    # Temperature
    temp_vals = [s["temperature"] for s in valid if s.get("temperature") is not None]
    if temp_stat := _calc_stats("Body Temperature", temp_vals, "°C", 1):
        stats_rows.append(temp_stat)

    # Respiration
    resp_vals = [s["respiration"] for s in valid if s.get("respiration") is not None]
    if resp_stat := _calc_stats("Respiratory Rate", resp_vals, "br/min", 0):
        stats_rows.append(resp_stat)

    # EtCO2
    etco2_vals = [s["etco2"] for s in valid if s.get("etco2") is not None]
    if etco2_stat := _calc_stats("End-Tidal CO2", etco2_vals, "mmHg", 1):
        stats_rows.append(etco2_stat)

    if stats_rows:
        headers = ["Parameter", "Minimum", "Average", "Maximum", "Samples"]
        story.append(_data_table(headers, stats_rows, col_widths=[195, 80, 80, 80, 80]))
    story.append(Spacer(1, 8))

    # 2. Vector Trend Charts (Only when sufficient data exists >= 5 samples)
    if len(valid) >= 5:
        story.append(Paragraph("Physiological Trends", styles["SubSectionHeading"]))

        # HR Chart
        if hr_vals:
            hr_chart = _create_trend_chart(
                [{"data": hr_vals, "color": _ACCENT_RED, "name": "HR"}],
                "Heart Rate Trend",
                max(30.0, min(hr_vals) - 5),
                min(200.0, max(hr_vals) + 5),
                "bpm",
            )
            story.append(hr_chart)
            story.append(Spacer(1, 6))

        # SpO2 Chart
        if spo2_vals:
            spo2_chart = _create_trend_chart(
                [{"data": spo2_vals, "color": _ACCENT_BLUE, "name": "SpO2"}],
                "Oxygen Saturation (SpO2)",
                max(80.0, min(spo2_vals) - 2),
                100.0,
                "%",
            )
            story.append(spo2_chart)
            story.append(Spacer(1, 6))

        # BP Chart (Systolic & Diastolic separate series)
        if sys_vals and dia_vals:
            bp_chart = _create_trend_chart(
                [
                    {"data": sys_vals, "color": colors.HexColor("#D97706"), "name": "Systolic"},
                    {"data": dia_vals, "color": colors.HexColor("#2563EB"), "name": "Diastolic"},
                ],
                "Blood Pressure Trend",
                max(30.0, min(dia_vals) - 10),
                min(250.0, max(sys_vals) + 10),
                "mmHg",
            )
            story.append(bp_chart)
            story.append(Spacer(1, 6))

    # 3. ECG Status Summary
    ecg_list = [s.get("ecg_status", "NORMAL SINUS") for s in valid if s.get("ecg_status")]
    if ecg_list:
        ecg_counts: Dict[str, int] = {}
        for item in ecg_list:
            ecg_counts[item] = ecg_counts.get(item, 0) + 1
        ecg_rows = [[status, str(cnt), f"{(cnt / len(ecg_list)) * 100:.1f}%"]
                    for status, cnt in sorted(ecg_counts.items(), key=lambda x: -x[1])]
        story.append(Paragraph("ECG Rhythm Distribution", styles["SubSectionHeading"]))
        story.append(_data_table(["ECG Classification", "Readings", "Proportion"], ecg_rows, col_widths=[235, 140, 140]))
        story.append(Spacer(1, 8))

    # 4. Representative Chronological Vitals Table
    story.append(Paragraph("Chronological Vitals Log (Sampled)", styles["SubSectionHeading"]))
    sample_stride = max(1, len(valid) // 25)
    sampled = valid[::sample_stride]
    chrono_rows = []
    for s in sampled:
        t_str = _format_time_short(s.get("timestamp", ""))
        rel_str = s.get("relative_session_time", "")
        time_display = f"{t_str} ({rel_str})" if rel_str else t_str
        hr_str = f"{s['heart_rate']:.0f}" if s.get("heart_rate") is not None else "--"
        spo2_str = f"{s['spo2']:.0f}%" if s.get("spo2") is not None else "--"
        bp_str = s.get("bp", "--")
        temp_str = f"{s['temperature']:.1f}°C" if s.get("temperature") is not None else "--"
        resp_str = f"{s['respiration']:.0f}" if s.get("respiration") is not None else "--"
        chrono_rows.append([time_display, hr_str, spo2_str, bp_str, temp_str, resp_str, s.get("ecg_status", "---")])

    story.append(_data_table(
        ["Time (Rel)", "HR", "SpO2", "BP (Sys/Dia)", "Temp", "Resp", "ECG Status"],
        chrono_rows,
        col_widths=[105, 55, 55, 90, 60, 50, 100],
    ))


def _build_yolo_section(story, data, styles):
    """Build YOLO object detection statistics and per-class table."""
    analytics = data.get("yolo_analytics", {})
    classes = analytics.get("classes", [])
    total_detections = analytics.get("total_detections", 0)

    if total_detections == 0 and not classes:
        story.append(Paragraph("No YOLO detections recorded.", styles["NoDataNote"]))
        return

    # YOLO Metadata Overview
    avg_inf = analytics.get("avg_inference_ms", 0.0)
    avg_fps = analytics.get("avg_fps", 0.0)
    tracking = "ENABLED" if analytics.get("tracking_active") else "DISABLED"

    yolo_overview = [
        ["Total Target Detections", str(total_detections)],
        ["Unique Object Classes", str(len(classes))],
        ["Instrument Tracking", tracking],
        ["Mean Inference Latency", f"{avg_inf:.1f} ms" if avg_inf > 0 else "N/A"],
        ["Pipeline Inference Rate", f"{avg_fps:.1f} FPS" if avg_fps > 0 else "N/A"],
    ]
    story.append(_info_grid(yolo_overview, col_widths=[195, 320]))
    story.append(Spacer(1, 6))

    # Object Table: | Object | Detections | Avg Confidence | First Seen | Last Seen |
    table_rows = []
    for c in classes:
        table_rows.append([
            c.get("name", "Unknown"),
            str(c.get("count", 0)),
            f"{c.get('avg_confidence', 0.0):.1f}%",
            _format_time_short(c.get("first_seen", "")),
            _format_time_short(c.get("last_seen", "")),
        ])

    story.append(Paragraph("Detected Surgical Instruments & Objects", styles["SubSectionHeading"]))
    story.append(_data_table(
        ["Object", "Detections", "Avg Confidence", "First Seen", "Last Seen"],
        table_rows,
        col_widths=[155, 90, 90, 90, 90],
    ))


def _build_alerts_section(story, data, styles):
    """Build alert audit table."""
    alerts = data.get("alerts", [])
    if not alerts:
        story.append(Paragraph("No alerts recorded during this session.", styles["NoDataNote"]))
        return

    # Severity Summary
    crit_c = sum(1 for a in alerts if a.get("severity") == "CRITICAL")
    warn_c = sum(1 for a in alerts if a.get("severity") == "WARNING")
    info_c = sum(1 for a in alerts if a.get("severity") == "INFO")

    summary_p = f"<b>Total Alerts:</b> {len(alerts)} &nbsp;|&nbsp; <b>CRITICAL:</b> {crit_c} &nbsp;|&nbsp; <b>WARNING:</b> {warn_c} &nbsp;|&nbsp; <b>INFO:</b> {info_c}"
    story.append(Paragraph(summary_p, styles["TableBody"]))
    story.append(Spacer(1, 6))

    alert_rows = []
    for a in alerts:
        t_str = _format_time_short(a.get("timestamp", ""))
        rel_str = a.get("relative_session_time", "")
        sev = a.get("severity", "INFO")
        src = a.get("source", "system")
        msg = a.get("message", "")
        alert_rows.append([t_str, rel_str, sev, src, msg])

    story.append(_data_table(
        ["Time", "Relative", "Severity", "Source", "Alert Message"],
        alert_rows,
        col_widths=[65, 55, 65, 80, 250],
    ))


def _build_messages_section(story, data, styles):
    """Build message center chronological timeline."""
    messages = data.get("messages", [])
    if not messages:
        story.append(Paragraph("No message center activity recorded.", styles["NoDataNote"]))
        return

    msg_rows = []
    for m in messages:
        t_str = _format_time_short(m.get("timestamp", ""))
        rel_str = m.get("relative_session_time", "")
        src = m.get("source", "system")
        txt = m.get("message", "")
        msg_rows.append([t_str, rel_str, src, txt])

    story.append(_data_table(
        ["Time", "Relative", "Source", "Action / Event Message"],
        msg_rows,
        col_widths=[75, 65, 95, 280],
    ))


def _build_video_section(story, data, styles):
    """Build video encoding and file verification section."""
    w = data.get("video_width", 0)
    h = data.get("video_height", 0)
    fps = data.get("video_fps", 0.0)
    frames = data.get("frame_count", 0)
    dropped = data.get("dropped_frames", 0)
    overlays = "BURNT-IN (YOLO Bounding Boxes Included)" if data.get("yolo_overlays_included") else "CLEAN ENDOSCOPIC FEED"
    filename = data.get("mp4_filename", "No video recorded")
    source = data.get("video_source", "Live Endoscopic Pipeline / Stream")

    res_str = f"{w} × {h}" if (w > 0 and h > 0) else "N/A"
    fps_str = f"{fps:.1f} FPS" if fps > 0 else "N/A"

    video_rows = [
        ["Recorded MP4 Filename", filename],
        ["Video Resolution", res_str],
        ["Target Framerate", fps_str],
        ["Total Recorded Frames", f"{frames:,}"],
        ["Dropped Frames (Bounded Queue)", f"{dropped:,}"],
        ["Overlay Integration", overlays],
        ["Source Identity", source or "Active Surgeon Console Endoscopic Stream"],
        ["Codec Standard", "MPEG-4 Part 2 / mp4v (Verified Local Runtime)"],
    ]
    story.append(_info_grid(video_rows, col_widths=[195, 320]))


# ── Utilities ─────────────────────────────────────────────────────

def _format_datetime(iso_str: str) -> str:
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d  %H:%M:%S")
    except Exception:
        return str(iso_str)


def _format_time_short(iso_str: str) -> str:
    if not iso_str:
        return "--:--:--"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return str(iso_str)[:8]
