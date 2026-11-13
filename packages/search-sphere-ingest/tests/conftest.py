from io import BytesIO

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def text_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), "1. Native extraction", fontsize=20)
    page.insert_text(
        (40, 100),
        "First sentence has exact evidence. Second sentence preserves context.",
        fontsize=11,
    )
    page = doc.new_page()
    page.insert_text((40, 60), "This paragraph continues under the previous heading.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def table_page(doc: fitz.Document, top: int, rows: list[list[str]], caption: str = "") -> None:
    page = doc.new_page(width=600, height=800)
    if caption:
        page.insert_text((40, top - 12), caption)
    for y in range(top, top + (len(rows) + 1) * 25, 25):
        page.draw_line((40, y), (400, y))
    for x in (40, 220, 400):
        page.draw_line((x, top), (x, top + len(rows) * 25))
    for row_no, row in enumerate(rows):
        for col_no, text in enumerate(row):
            page.insert_text((45 + col_no * 180, top + row_no * 25 + 16), text, fontsize=10)


@pytest.fixture
def table_pdf() -> bytes:
    doc = fitz.open()
    table_page(
        doc, 690, [["Item", "Value (mg)"], ["A-101", "120"], ["B-102", "80"]], "Table 1: Values"
    )
    table_page(doc, 40, [["Item", "Value (mg)"], ["C-103", "42"], ["D-104", "61"]])
    data = doc.tobytes()
    doc.close()
    return data


def panel_image() -> bytes:
    image = Image.new("RGB", (900, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 50, 390, 340), outline="black", width=3)
    draw.rectangle((510, 50, 870, 340), outline="black", width=3)
    draw.rectangle((60, 190, 100, 320), fill="blue")
    draw.rectangle((540, 100, 580, 320), fill="red")
    draw.text((50, 20), "Panel A: 10", fill="black")
    draw.text((530, 20), "Panel B: 20", fill="black")
    data = BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def scan_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=900, height=400)
    page.insert_image(page.rect, stream=panel_image())
    data = doc.tobytes()
    doc.close()
    return data
