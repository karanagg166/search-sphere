from src.processing.cleaning.hyphenation_cleaner import HyphenationCleaner


def test_hyphenation_cleaner_empty_input() -> None:
    cleaner = HyphenationCleaner()
    assert cleaner.clean("") == ""


def test_hyphenation_cleaner_repairs_split_word() -> None:
    cleaner = HyphenationCleaner()
    text = "This docu-\nment explains seman-\n  tic search."
    assert cleaner.clean(text) == "This document explains semantic search."


def test_hyphenation_cleaner_preserves_inline_compounds() -> None:
    cleaner = HyphenationCleaner()
    text = "We use a state-of-the-art and high-level system."
    assert cleaner.clean(text) == "We use a state-of-the-art and high-level system."


def test_hyphenation_cleaner_capitalized_split_compounds() -> None:
    cleaner = HyphenationCleaner()
    text = "The Anglo-\nAmerican alliance."
    assert cleaner.clean(text) == "The Anglo-American alliance."


def test_hyphenation_cleaner_soft_hyphen() -> None:
    cleaner = HyphenationCleaner()
    text = "multi\u00admodal and infor\u00ad\nmation"
    assert cleaner.clean(text) == "multimodal and information"


def test_hyphenation_cleaner_preserves_bullet_lists() -> None:
    cleaner = HyphenationCleaner()
    text = "- First item\n- Second item"
    assert cleaner.clean(text) == "- First item\n- Second item"
