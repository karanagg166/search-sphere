"""Valid synthetic files; a signature alone is not a complete document."""
from io import BytesIO

import fitz
from PIL import Image


def pdf_bytes():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 70), "Synthetic laboratory report: HbA1c 7.4%")
        return pdf.tobytes()


def image_bytes(format):
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format=format)
    return buffer.getvalue()


def table_pdf():
    pdf = fitz.open()
    page = pdf.new_page()
    for x in [40, 200, 350]:
        page.draw_line((x, 100), (x, 220))
    for y in [100, 130, 160, 190, 220]:
        page.draw_line((40, y), (350, y))
    for row, values in enumerate([["Item", "Value (mg)"], ["A-101", "120"], ["B-102", "80"], ["C-103", "42"]]):
        for col, value in enumerate(values):
            page.insert_text((50 + col * 160, 120 + row * 30), value)
    page.insert_text((40, 78), "Table 1: Public synthetic units")
    page.insert_text((40, 270), "Public prose outside the table remains available.")
    content = pdf.tobytes()
    pdf.close()
    return content
