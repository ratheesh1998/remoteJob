import io

from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

CV_LINES = [
    "Jane Doe",
    "Senior Python Developer",
    "jane.doe@example.com | +1 415 555 0134 | Lisbon, Portugal",
    "",
    "Summary: Backend engineer with 8 years of experience building APIs.",
    "Skills: Python, Django, PostgreSQL, Docker, Celery, Redis, AWS",
    "Experience",
    "Senior Python Developer, Acme Corp (2019 - present)",
    "Built Django REST services backed by PostgreSQL and deployed with Docker on AWS.",
]


def make_pdf(lines=CV_LINES) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


def make_scanned_pdf() -> bytes:
    """A PDF page with only a drawn shape and no text layer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.rect(100, 100, 300, 300, fill=1)
    c.save()
    return buf.getvalue()


def make_docx(lines=CV_LINES) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
