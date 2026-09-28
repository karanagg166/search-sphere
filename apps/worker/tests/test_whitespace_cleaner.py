from src.processing.cleaning.whitespace_cleaner import WhitespaceCleaner


def test_whitespace_cleaner_empty_input() -> None:
    cleaner = WhitespaceCleaner()
    assert cleaner.clean("") == ""
    assert cleaner.normalize_spacing("") == ""


def test_whitespace_cleaner_unicode_nfkc() -> None:
    cleaner = WhitespaceCleaner()
    # Ligature 'ﬁ' should normalize to 'fi'
    assert cleaner.clean("ﬁle") == "file"
    # Full-width Latin letters should normalize to standard ASCII
    assert cleaner.clean("Ｓｅａｒｃｈ") == "Search"


def test_whitespace_cleaner_invisible_and_zero_width_chars() -> None:
    cleaner = WhitespaceCleaner()
    # Contains \u200b (zero-width space) and \ufeff (BOM)
    text = "Search\u200b\ufeffSphere"
    assert cleaner.clean(text) == "SearchSphere"


def test_whitespace_cleaner_non_breaking_spaces() -> None:
    cleaner = WhitespaceCleaner()
    # \u00a0 (NBSP) and \u202f (narrow NBSP)
    text = "Semantic\u00a0search\u202fsystem"
    assert cleaner.clean(text) == "Semantic search system"


def test_whitespace_cleaner_carriage_returns() -> None:
    cleaner = WhitespaceCleaner()
    text = "Line 1\r\nLine 2\rLine 3\nLine 4"
    assert cleaner.clean(text) == "Line 1\nLine 2\nLine 3\nLine 4"


def test_whitespace_cleaner_control_characters() -> None:
    cleaner = WhitespaceCleaner()
    # \x00 (null), \x07 (bell), \x1b (escape)
    text = "Clean\x00this\x07text\x1bnow"
    assert cleaner.clean(text) == "Cleanthistextnow"


def test_whitespace_cleaner_tabs_and_horizontal_spaces() -> None:
    cleaner = WhitespaceCleaner()
    text = "This     is\t\tSearch     Sphere."
    assert cleaner.clean(text) == "This is Search Sphere."


def test_whitespace_cleaner_trailing_line_spaces() -> None:
    cleaner = WhitespaceCleaner()
    text = "Line one   \nLine two     \nLine three"
    assert cleaner.clean(text) == "Line one\nLine two\nLine three"


def test_whitespace_cleaner_paragraph_break_preservation() -> None:
    cleaner = WhitespaceCleaner()
    text = "Paragraph one\n\n\n\n\nParagraph two"
    assert cleaner.clean(text) == "Paragraph one\n\nParagraph two"
