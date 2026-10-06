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
