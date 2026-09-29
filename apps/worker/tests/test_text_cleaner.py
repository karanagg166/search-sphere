from src.processing.cleaning import TextCleaner
from src.processing.models.document import (
    CleanedDocument,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)


def test_text_cleaner_empty_input() -> None:
    cleaner = TextCleaner()
    assert cleaner.clean("") == ""


def test_text_cleaner_pipeline_integration() -> None:
    cleaner = TextCleaner()
    raw_text = (
        "Semantic\u00a0Search   System\u200b\n"
        "-----------------------\n"
        "This docu-\n"
        "ment explains how semantic\n"
        "search retrieves relevant\n"
        "documents from PDFs.\n\n"
        "Key features:\n"
        "- Native text extraction\n"
        "- OCR image reading\n"
        "- BLIP captions\n\n"
        "[Image text: Q3 Growth: 28%]\n\n"
        "[Image description: A bar chart illustrating quarterly revenue growth]"
    )

    cleaned = cleaner.clean(raw_text)

    # 1. Non-breaking space normalized, zero-width space removed
    assert "Semantic Search System" in cleaned
    # 2. Separator line removed
    assert "-----------------------" not in cleaned
    # 3. Hyphenation repaired
    assert "This document explains" in cleaned
    # 4. Soft line wrap unwrapped
    assert "how semantic search retrieves relevant documents from PDFs." in cleaned
    # 5. List items preserved
    assert "- Native text extraction" in cleaned
    assert "- OCR image reading" in cleaned
    # 6. Multimodal markers strictly preserved
    assert "[Image text: Q3 Growth: 28%]" in cleaned
    assert (
        "[Image description: A bar chart illustrating quarterly revenue growth]"
        in cleaned
    )


def test_text_cleaner_clean_block_text() -> None:
    cleaner = TextCleaner()
    block = ExtractedBlock(
        block_type="text",
        content="This   is\u00a0a\ntext block.",
    )
    cleaned_block = cleaner.clean_block(block)
    assert cleaned_block is not None
    assert cleaned_block.block_type == "text"
    assert cleaned_block.content == "This is a text block."


def test_text_cleaner_clean_block_empty() -> None:
    cleaner = TextCleaner()
    block = ExtractedBlock(
        block_type="text",
        content="   \n\n   ",
    )
    cleaned_block = cleaner.clean_block(block)
    assert cleaned_block is None


def test_text_cleaner_clean_block_image_multimodal() -> None:
    cleaner = TextCleaner()
    block = ExtractedBlock(
        block_type="image",
        content=(
            "[Image text:   Revenue   Q4  ]\n\n"
            "[Image description:   A financial  bar  chart  ]"
        ),
    )
    cleaned_block = cleaner.clean_block(block)
    assert cleaned_block is not None
    assert cleaned_block.block_type == "image"
    expected = "[Image text: Revenue Q4]\n\n[Image description: A financial bar chart]"
    assert cleaned_block.content == expected


def test_text_cleaner_clean_page_and_document() -> None:
    cleaner = TextCleaner()

    page1_text = "Title\u00a01\n-----------------------\nIntroductory para-\ngraph."

    page1 = ExtractedPage(
        page_number=1,
        blocks=[
            ExtractedBlock(
                block_type="text",
                content=page1_text,
            ),
            ExtractedBlock(
                block_type="image",
                content="[Image description: System diagram]",
            ),
            ExtractedBlock(
                block_type="text",
                content="   ",  # Empty block, should be dropped
            ),
        ],
    )

    page2 = ExtractedPage(
        page_number=2,
        blocks=[
            ExtractedBlock(
                block_type="text",
                content="Page two   content.",
            )
        ],
    )

    doc = ExtractedDocument(pages=[page1, page2])
    cleaned_doc = cleaner.clean_document(doc)

    assert isinstance(cleaned_doc, CleanedDocument)
    assert len(cleaned_doc.pages) == 2

    # Page 1 checks
    p1 = cleaned_doc.pages[0]
    assert p1.page_number == 1
    assert len(p1.blocks) == 2  # empty block was pruned
    assert p1.blocks[0].content == "Title 1\nIntroductory paragraph."
    assert p1.blocks[1].content == "[Image description: System diagram]"

    # Combined text check
    combined = cleaned_doc.combined_text()
    assert "Title 1\nIntroductory paragraph." in combined
    assert "[Image description: System diagram]" in combined
    assert "Page two content." in combined
