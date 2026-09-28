import re

import structlog

logger = structlog.get_logger()

# Regex to detect lines that are purely repeated separator symbols (3+ chars)
# e.g., "-----------------------", "====================", "________", "••••••••"
_SEPARATOR_LINE_RE = re.compile(r"^[\s\-_=\*~•\.\/\\\|\#]{3,}$")

# Pattern to check if line contains any meaningful alphanumeric characters
_ALPHANUMERIC_RE = re.compile(r"[a-zA-Z0-9]")


class NoiseCleaner:
    """
    Removes low-information extraction noise and visual separators.

    Responsibilities:
    - Remove zero-information horizontal rules/separator lines ('------', '=====');
    - Remove standalone symbol artifacts;
    - Strictly preserve multimodal markers ([Image text:...], [Image description:...]);
    - Strictly preserve section titles, bulleted items, and meaningful text.
    """

    def clean(self, text: str) -> str:
        """Remove obvious noise lines while preserving document text and markers."""
        if not text:
            return ""

        paragraphs = text.split("\n\n")
        cleaned_paragraphs: list[str] = []

        for paragraph in paragraphs:
            cleaned_lines = self._clean_paragraph_lines(paragraph)
            if cleaned_lines:
                cleaned_paragraphs.append("\n".join(cleaned_lines))

        return "\n\n".join(cleaned_paragraphs).strip()

    def _clean_paragraph_lines(self, paragraph: str) -> list[str]:
        cleaned: list[str] = []

        for line in paragraph.split("\n"):
            trimmed = line.strip()
            if not trimmed:
                continue

            if self._is_noise(trimmed):
                logger.debug("Removing extraction noise line", line=trimmed[:30])
                continue

            cleaned.append(trimmed)

        return cleaned

    def _is_noise(self, line: str) -> bool:
        # Multimodal annotations are critical domain content, never noise
        if self._is_multimodal_marker(line):
            return False

        # If line contains alphanumeric chars, it is legitimate text ('Section 1')
        if _ALPHANUMERIC_RE.search(line):
            return False

        # Lines consisting purely of repeated separator symbols without text
        if _SEPARATOR_LINE_RE.match(line):
            return True

        # Any sequence of 2+ punctuation characters without alphanumeric content
        if len(line) >= 2 and not _ALPHANUMERIC_RE.search(line):
            return True

        return False

    @staticmethod
    def _is_multimodal_marker(line: str) -> bool:
        return line.startswith("[Image text:") or line.startswith("[Image description:")
