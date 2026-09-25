from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

class DictationPDF:
    def generate(self, output_file: str, segments, duration_seconds: float):
        path = Path(output_file)
        path.parent.mkdir(parents=True, exist_ok=True)

        document = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            rightMargin=15 * mm,
            leftMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
        )

        styles = getSampleStyleSheet()
        story = []

        story.append(
            Paragraph(
                "AETHER SURGICAL CONSOLE",
                styles["Title"],
            )
        )

        story.append(
            Paragraph(
                "SURGEON DICTATION",
                styles["Heading2"],
            )
        )

        story.append(Spacer(1, 8 * mm))

        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)

        story.append(
            Paragraph(
                f"Duration: {minutes:02d}:{seconds:02d}",
                styles["Normal"],
            )
        )

        story.append(Spacer(1, 6 * mm))

        data = [["TIME", "DICTATION"]]

        for segment in segments:
            data.append([
                segment.timestamp,
                segment.text,
            ])

        if len(data) == 1:
            data.append(["-", "No dictation recorded."])

        table = Table(
            data,
            colWidths=[42 * mm, 130 * mm],
            repeatRows=1,
        )

        table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#20252B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )

        story.append(table)
        story.append(Spacer(1, 8 * mm))
        story.append(
            Paragraph(
                "End of Dictation",
                styles["Normal"],
            )
        )

        document.build(story)

        return str(path)
