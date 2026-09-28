from src.processing.cleaning.line_cleaner import LineCleaner


def test_line_cleaner_empty_input() -> None:
    cleaner = LineCleaner()
    assert cleaner.clean("") == ""


def test_line_cleaner_unwraps_wrapped_sentence() -> None:
    cleaner = LineCleaner()
    text = "The system uses semantic\nsearch to retrieve relevant\ndocuments."
    expected = "The system uses semantic search to retrieve relevant documents."
    assert cleaner.clean(text) == expected


def test_line_cleaner_preserves_structural_colons_and_capitals() -> None:
    cleaner = LineCleaner()
    text = "Features:\nFast search\nOCR support\nImage captions"
    assert cleaner.clean(text) == text


def test_line_cleaner_preserves_bullet_lists() -> None:
    cleaner = LineCleaner()
    text = "Architecture\n\n- API\n- RabbitMQ\n- Worker\n- PostgreSQL"
    assert cleaner.clean(text) == text


def test_line_cleaner_preserves_numbered_lists() -> None:
    cleaner = LineCleaner()
    text = "Pipeline steps:\n\n1. Extract PDF\n2. Clean text\n3. Chunk text"
    assert cleaner.clean(text) == text


def test_line_cleaner_preserves_multimodal_markers() -> None:
    cleaner = LineCleaner()
    text = (
        "The architecture overview is illustrated below:\n\n"
        "[Image text: Q3 revenue grew 28%]\n\n"
        "[Image description: A bar chart showing quarterly revenue growth]\n\n"
        "Further analysis confirms continuous growth."
    )
    assert cleaner.clean(text) == text


def test_line_cleaner_preserves_markdown_headings() -> None:
    cleaner = LineCleaner()
    text = "# Document Cleaning\n\n## Overview\n\nThis is an introductory paragraph."
    assert cleaner.clean(text) == text
