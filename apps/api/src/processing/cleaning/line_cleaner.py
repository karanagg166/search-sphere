import re

import structlog

logger = structlog.get_logger()

# List markers: bullet points (-, *, •, +), numbers (1., 1)), or letters ((a), (1))
_LIST_MARKER_RE = re.compile(r"^\s*([-*•+]\s+|\d+[\.\)]\s+|\([a-zA-Z0-9]+\)\s+)")

# Sentence-terminal punctuation
_SENTENCE_TERMINAL_PUNCTUATION = (".", "!", "?", ":", ";")


class LineCleaner:
    """
    Normalizes soft visual line wraps from PDF extraction preserving structure.

    Responsibilities:
    - Reconnect wrapped sentences split by column margins;
    - Preserve list items (bullets, numbering);
    - Preserve headings and title structures;
    - Preserve multimodal markers ([Image text:...], [Image description:...]);
    - Preserve paragraph breaks.
    """

    def clean(self, text: str) -> str:
        """Unwrap soft line-breaks using conservative heuristics."""
        if not text:
            return ""

        # Process each paragraph independently so paragraph breaks are preserved
        paragraphs = text.split("\n\n")
        cleaned_paragraphs: list[str] = []

        for paragraph in paragraphs:
            cleaned_paragraph = self._clean_paragraph(paragraph)
            if cleaned_paragraph:
                cleaned_paragraphs.append(cleaned_paragraph)

        return "\n\n".join(cleaned_paragraphs)

    def _clean_paragraph(self, paragraph: str) -> str:
        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        if not lines:
            return ""

        if len(lines) == 1:
            return lines[0]

        merged_lines: list[str] = [lines[0]]

        for current_line in lines[1:]:
            prev_line = merged_lines[-1]

            if self._should_unwrap_lines(prev_line, current_line):
                # Join unwrapped line to the previous line with a single space
                merged_lines[-1] = f"{prev_line} {current_line}"
            else:
                merged_lines.append(current_line)

        return "\n".join(merged_lines)

    def _should_unwrap_lines(self, prev_line: str, next_line: str) -> bool:
        """
        Conservative heuristic to decide if two lines form a wrapped sentence.

        Unwraps only if:
        1. Neither line is a multimodal marker;
        2. Neither line is a list item;
        3. Neither line is a markdown heading;
        4. Previous line does not end with sentence-ending punctuation;
        5. Next line starts with a lowercase letter (standard continuation).
        """
        if self._is_multimodal_marker(prev_line) or self._is_multimodal_marker(
            next_line
        ):
            return False

        if self._is_list_item(prev_line) or self._is_list_item(next_line):
            return False

        if prev_line.startswith("#") or next_line.startswith("#"):
            return False

        # If previous line ends with terminal punctuation (: or . or ! or ? or ;)
        if prev_line.endswith(_SENTENCE_TERMINAL_PUNCTUATION):
            return False

        # If next line starts with lowercase text, it continues previous line
        first_char = next_line[0]
        if first_char.islower():
            return True

        return False

    @staticmethod
    def _is_multimodal_marker(line: str) -> bool:
        return line.startswith("[Image text:") or line.startswith("[Image description:")

    @staticmethod
    def _is_list_item(line: str) -> bool:
        return bool(_LIST_MARKER_RE.match(line))
