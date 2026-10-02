import re
import unicodedata

import structlog

logger = structlog.get_logger()

# Regex to detect and strip non-printable ASCII control characters except \t and \n
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Common invisible and zero-width characters:
# \u200b (ZWSP), \u200c (ZWNJ), \u200d (ZWJ), \ufeff (BOM), \u200e (LRM), \u200f (RLM)
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u200e\u200f\u202a-\u202e]")

# Unconventional spaces to regular space:
# \u00a0 (NBSP), \u202f (narrow NBSP), \u205f (math), \u3000 (ideographic)
_UNUSUAL_SPACES_RE = re.compile(r"[\u00a0\u202f\u205f\u3000\u2000-\u200a]")

# Horizontal whitespace (excluding newlines)
_HORIZONTAL_SPACES_RE = re.compile(r"[^\S\n]+")

# 3 or more consecutive newlines collapsed to maximum 2 newlines (paragraph boundary)
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")


class WhitespaceCleaner:
    """
    Normalizes Unicode encoding, invisible characters, and whitespace.

    Responsibilities:
    - Normalize Unicode with NFKC;
    - Replace non-breaking and unusual spaces with standard spaces;
    - Strip invisible/zero-width control characters;
    - Standardize line endings (\\r\\n and \\r -> \\n);
    - Expand tabs to spaces;
    - Collapse multiple horizontal spaces to a single space;
    - Remove trailing whitespace per line;
    - Limit consecutive blank lines to maximum two (preserves paragraph breaks).
    """

    def clean(self, text: str) -> str:
        """Clean and normalize Unicode and whitespace in text."""
        if not text:
            return ""

        # 1. Unicode normalization (NFKC decomposes compatibility and canonicals)
        normalized = unicodedata.normalize("NFKC", text)

        # 2. Line ending normalization
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

        # 3. Strip invisible / zero-width characters
        normalized = _INVISIBLE_CHARS_RE.sub("", normalized)

        # 4. Convert unusual unicode spaces to standard ASCII space
        normalized = _UNUSUAL_SPACES_RE.sub(" ", normalized)

        # 5. Remove ASCII control characters (keeping \t and \n)
        normalized = _CONTROL_CHAR_RE.sub("", normalized)

        # 6. Convert tabs to 4 spaces
        normalized = normalized.replace("\t", "    ")

        # 7. Collapse horizontal whitespace per line and strip trailing whitespace
        lines = [
            _HORIZONTAL_SPACES_RE.sub(" ", line).rstrip()
            for line in normalized.split("\n")
        ]
        normalized = "\n".join(lines)

        # 8. Collapse runaway blank lines to max 2 newlines (paragraph breaks)
        normalized = _EXCESS_NEWLINES_RE.sub("\n\n", normalized)

        return normalized.strip()

    def normalize_spacing(self, text: str) -> str:
        """
        Lightweight tidy of redundant spacing after intermediate pipeline transforms.
        """
        if not text:
            return ""
        lines = [
            _HORIZONTAL_SPACES_RE.sub(" ", line).rstrip() for line in text.split("\n")
        ]
        cleaned = "\n".join(lines)
        return _EXCESS_NEWLINES_RE.sub("\n\n", cleaned).strip()
