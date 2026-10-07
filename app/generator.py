import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f2a44")
GOLD = colors.HexColor("#b08d57")


def _fit_font_size(text: str, font: str, start: int, max_width: float) -> int:
    size = start
    while size > 12 and stringWidth(text, font, size) > max_width:
        size -= 2
    return size


def generate_certificate_pdf(
    path: Path,
    *,
    recipient_name: str,
    title: str,
    event_name: str,
    issued_by: str,
    issue_date: date,
    certificate_number: str,
) -> None:
    """Draws the (single) certificate template and writes it to `path`.

    Written to a temp file first and then renamed, so a crash half way
    never leaves a broken PDF that looks like a finished one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".pdf.tmp")

    c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"{title} - {recipient_name}")
    cx = PAGE_W / 2
    max_text_width = PAGE_W - 160

    # double border
    c.setStrokeColor(GOLD)
    c.setLineWidth(4)
    c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1)
    c.rect(36, 36, PAGE_W - 72, PAGE_H - 72)

    c.setFillColor(NAVY)
    size = _fit_font_size(title, "Helvetica-Bold", 38, max_text_width)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(cx, PAGE_H - 130, title.upper())

    c.setFillColor(colors.grey)
    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 185, "This is proudly presented to")

    c.setFillColor(NAVY)
    size = _fit_font_size(recipient_name, "Helvetica-BoldOblique", 44, max_text_width)
    c.setFont("Helvetica-BoldOblique", size)
    c.drawCentredString(cx, PAGE_H - 250, recipient_name)

    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.line(cx - 200, PAGE_H - 265, cx + 200, PAGE_H - 265)

    c.setFillColor(colors.grey)
    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 305, "for successfully completing")

    c.setFillColor(NAVY)
    size = _fit_font_size(event_name, "Helvetica-Bold", 26, max_text_width)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(cx, PAGE_H - 345, event_name)

    # footer
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 12)
    c.drawString(90, 95, f"Date: {issue_date.strftime('%d %B %Y')}")
    c.drawRightString(PAGE_W - 90, 95, f"Issued by: {issued_by}")
    c.setFillColor(colors.grey)
    c.setFont("Helvetica", 9)
    c.drawCentredString(cx, 60, f"Certificate No. {certificate_number}")

    c.showPage()
    c.save()
    os.replace(tmp_path, path)
