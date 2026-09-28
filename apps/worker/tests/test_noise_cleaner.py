from src.processing.cleaning.noise_cleaner import NoiseCleaner


def test_noise_cleaner_empty_input() -> None:
    cleaner = NoiseCleaner()
    assert cleaner.clean("") == ""


def test_noise_cleaner_removes_repeated_symbol_dividers() -> None:
    cleaner = NoiseCleaner()
    text = (
        "Section 1\n"
        "-----------------------\n"
        "This is valid content.\n"
        "=======================\n"
        "•••••••••••••••••••••••\n"
        "More valid content.\n"
        "_______________________"
    )
    expected = "Section 1\nThis is valid content.\nMore valid content."
    assert cleaner.clean(text) == expected


def test_noise_cleaner_preserves_section_names_and_numbers() -> None:
    cleaner = NoiseCleaner()
    text = "Section 1\n\nChapter 2.3: System Specifications\n\nPart IV"
    assert cleaner.clean(text) == text


def test_noise_cleaner_strictly_preserves_multimodal_markers() -> None:
    cleaner = NoiseCleaner()
    text = (
        "Overview paragraph\n\n"
        "-------------------\n\n"
        "[Image text: Q3 revenue grew 28%]\n\n"
        "[Image description: A bar chart showing quarterly revenue growth]\n\n"
        "===================\n\n"
        "Summary text"
    )
    expected = (
        "Overview paragraph\n\n"
        "[Image text: Q3 revenue grew 28%]\n\n"
        "[Image description: A bar chart showing quarterly revenue growth]\n\n"
        "Summary text"
    )
    assert cleaner.clean(text) == expected
