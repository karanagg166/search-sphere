import re

import structlog

logger = structlog.get_logger()

# Soft hyphen character
_SOFT_HYPHEN = "\u00ad"

# Matches words split by hyphen at a line break where continuation is lowercase:
# e.g., "docu-\nment" -> "document", "seman-\n  tic" -> "semantic"
_LOWER_HYPHEN_BREAK_RE = re.compile(r"(\b[a-zA-Z]+)-\s*\n\s*([a-z]+)\b")

# Matches capitalized compound words split across lines:
# e.g., "Anglo-\nAmerican" -> "Anglo-American"
_CAPITAL_HYPHEN_BREAK_RE = re.compile(r"(\b[A-Z][a-zA-Z]*)-\s*\n\s*([A-Z][a-zA-Z]*)\b")


class HyphenationCleaner:
    """
    Repairs hyphenated words split across line breaks during PDF extraction.

    Responsibilities:
    - Reconnect words broken by line-wrap hyphens ('docu-\\nment' -> 'document');
    - Preserve genuine hyphenated compounds on same line ('state-of-the-art');
    - Preserve capitalized compound words across lines ('Anglo-\\nAmerican');
    - Clean soft hyphens (\\u00ad);
    - Avoid touching list markers (e.g. '- Item').
    """

    def clean(self, text: str) -> str:
        """Repair line-break hyphenation while preserving compound hyphens."""
        if not text:
            return ""

        # Remove soft hyphen line breaks and standalone soft hyphens
        normalized = text.replace(f"{_SOFT_HYPHEN}\n", "").replace(_SOFT_HYPHEN, "")

        # Repair lowercase wrapped words: docu-\nment -> document
        normalized = _LOWER_HYPHEN_BREAK_RE.sub(r"\1\2", normalized)

        # Repair capitalized compounds split across lines: Anglo-\nAmerican
        normalized = _CAPITAL_HYPHEN_BREAK_RE.sub(r"\1-\2", normalized)

        return normalized
